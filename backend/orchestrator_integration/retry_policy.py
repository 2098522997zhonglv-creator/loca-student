"""集中重试 / 核对策略（FR06.2）。

依据 Catalog.retry_policy + 通道预算 + ToolResult.retryable 决策，避免散落 if。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from orchestrator_integration.skill_catalog import load_skill_catalog
from orchestrator_integration.task_commission import CHANNEL_LIGHT


@dataclass(frozen=True)
class RetryDecision:
    allow: bool
    reason: str
    next_action: str = ""  # retry | reconcile | stop | heal
    max_attempts: int = 0


def decide_retry(
    *,
    skill_name: str,
    attempt: int,
    channel: str = CHANNEL_LIGHT,
    tool_retryable: Optional[bool] = None,
    error_text: str = "",
    commission_light_retries: int = 1,
) -> RetryDecision:
    """返回是否允许再试，以及建议动作。"""
    catalog = load_skill_catalog()
    entry = catalog.get(skill_name) if skill_name else None
    policy = (entry.retry_policy if entry else "none") or "none"
    reconcile = (entry.reconcile_action if entry else "") or ""

    # 业务类错误：不重试，可核对
    blob = (error_text or "").lower()
    business_markers = (
        "余额不足",
        "库存不足",
        "权限不足",
        "无权限",
        "已存在",
        "业务校验",
        "rejected by policy",
        "needs_confirmation",
    )
    if any(m.lower() in blob for m in business_markers):
        if reconcile:
            return RetryDecision(
                allow=False,
                reason="business_error_reconcile",
                next_action="reconcile",
                max_attempts=0,
            )
        return RetryDecision(False, "business_error", "stop", 0)

    # filechooser 超时：优先自愈换策略，不算盲目重试
    if "filechooser" in blob and ("timeout" in blob or "超时" in blob):
        return RetryDecision(
            allow=True,
            reason="filechooser_timeout_heal",
            next_action="heal",
            max_attempts=1,
        )

    if policy == "none":
        # 轻量通道仍允许 1 次通用重试（与 commission.max_light_retries 对齐）
        if channel == CHANNEL_LIGHT and attempt < max(0, commission_light_retries):
            if tool_retryable is False:
                return RetryDecision(False, "not_retryable", "stop", 0)
            return RetryDecision(
                True,
                "light_channel_once",
                "retry",
                max_attempts=commission_light_retries,
            )
        return RetryDecision(False, "policy_none", "stop", 0)

    if policy == "once":
        max_a = 1
    elif policy == "bounded":
        max_a = 3
    else:
        max_a = 0

    if channel == CHANNEL_LIGHT:
        max_a = min(max_a, max(0, commission_light_retries))

    if tool_retryable is False:
        return RetryDecision(False, "not_retryable", "stop", max_a)

    if attempt < max_a:
        return RetryDecision(True, f"catalog_{policy}", "retry", max_a)

    if reconcile:
        return RetryDecision(False, "exhausted_reconcile", "reconcile", max_a)
    return RetryDecision(False, "exhausted", "stop", max_a)


def format_retry_hint(decision: RetryDecision) -> str:
    if decision.next_action == "heal":
        return "工具超时：请换策略自愈（如 filechooser→setInputFiles），不要原样重放。"
    if decision.next_action == "reconcile" and decision.allow is False:
        return "重试已用尽：请用核对动作确认终态，勿盲目再调同一写操作。"
    if decision.allow and decision.next_action == "retry":
        return f"允许有界重试（reason={decision.reason}）。"
    return "不可重试：用已有证据交付或 report_task_blocked。"


def decide_retry_dict(**kwargs: Any) -> Dict[str, Any]:
    d = decide_retry(**kwargs)
    return {
        "allow": d.allow,
        "reason": d.reason,
        "next_action": d.next_action,
        "max_attempts": d.max_attempts,
        "hint": format_retry_hint(d),
    }
