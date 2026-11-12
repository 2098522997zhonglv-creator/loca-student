"""故障后核对（FR06.3 / AC09 / AC13）。

超时或崩溃恢复时：先查原动作是否已生效，禁止盲目重放。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from orchestrator_integration.retry_policy import decide_retry
from orchestrator_integration.skill_catalog import load_skill_catalog

_TIMEOUT_RE = re.compile(r"timeout|timed out|超时|连接断开|ConnectionError", re.I)
_EXEC_ID_RE = re.compile(
    r"(?:execution[_-]?id|run[_-]?id|record[_-]?id)[=:\s]+([A-Za-z0-9_-]+)",
    re.I,
)
_CASE_ID_RE = re.compile(
    r"(?:testcase[_-]?id|case[_-]?id|ui[_-]?case[_-]?id)[=:\s]+([A-Za-z0-9_-]+)",
    re.I,
)


def is_uncertain_result(text: str = "", status: str = "") -> bool:
    st = (status or "").lower()
    if st in {"unknown"}:
        return True
    blob = text or ""
    if _TIMEOUT_RE.search(blob) and not any(
        k in blob for k in ("succeeded", "cart_id", "order_sn", "终态", "completed")
    ):
        return True
    if '"status": "unknown"' in blob or '"status":"unknown"' in blob:
        return True
    return False


def extract_reconcile_keys(text: str = "", structured: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    keys: Dict[str, str] = {}
    data = (structured or {}).get("data") if isinstance(structured, dict) else None
    if isinstance(data, dict):
        for k in ("execution_id", "run_id", "testcase_id", "case_id", "operation_id"):
            if data.get(k):
                keys[k] = str(data[k])
    blob = text or ""
    if "execution_id" not in keys:
        m = _EXEC_ID_RE.search(blob)
        if m:
            keys["execution_id"] = m.group(1)
    if "testcase_id" not in keys:
        m = _CASE_ID_RE.search(blob)
        if m:
            keys["testcase_id"] = m.group(1)
    if isinstance(structured, dict) and structured.get("operation_id"):
        keys.setdefault("operation_id", str(structured["operation_id"]))
    return keys


def build_reconcile_plan(
    *,
    skill_name: str,
    text: str = "",
    status: str = "",
    structured: Optional[Dict[str, Any]] = None,
    attempt: int = 0,
    channel: str = "light",
) -> Dict[str, Any]:
    """给出核对/重试决策，供恢复与工具层使用。"""
    catalog = load_skill_catalog()
    entry = catalog.get(skill_name) if skill_name else None
    uncertain = is_uncertain_result(text, status)
    keys = extract_reconcile_keys(text, structured)
    reconcile_action = (entry.reconcile_action if entry else "") or ""
    decision = decide_retry(
        skill_name=skill_name or "",
        attempt=attempt,
        channel=channel,
        tool_retryable=False if uncertain else None,
        error_text=text or status,
    )

    if uncertain:
        return {
            "must_reconcile": True,
            "allow_replay": False,
            "reconcile_action": reconcile_action or "query_original_record",
            "resource_keys": keys,
            "reason": "uncertain_result_reconcile_first",
            "hint": (
                "结果不确定（超时/断连）：禁止重新启动同一动作；"
                f"请先用 {reconcile_action or '原记录查询'} 核对"
                + (
                    f"（keys={keys}）"
                    if keys
                    else "（若无 execution_id，用业务唯一键/时间窗口查询）"
                )
                + "。核对成功则补齐证据；明确失败后再按策略有界重试。"
            ),
            "retry": {
                "allow": decision.allow,
                "next_action": decision.next_action,
                "reason": decision.reason,
            },
        }

    return {
        "must_reconcile": False,
        "allow_replay": decision.allow and decision.next_action == "retry",
        "reconcile_action": reconcile_action,
        "resource_keys": keys,
        "reason": decision.reason,
        "hint": (
            "允许有界重试。"
            if decision.allow
            else (
                f"请先核对（{reconcile_action}）再决定。"
                if decision.next_action == "reconcile" and reconcile_action
                else "不可盲目重放。"
            )
        ),
        "retry": {
            "allow": decision.allow,
            "next_action": decision.next_action,
            "reason": decision.reason,
        },
    }


def collect_uncertain_steps(steps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从步骤快照中找出需核对项（崩溃恢复入口）。"""
    out: List[Dict[str, Any]] = []
    for s in steps or []:
        status = str(s.get("status") or "")
        summary = str(s.get("tool_output_summary") or "")
        structured = s.get("structured_result") or {}
        if status == "unknown" or is_uncertain_result(summary, status):
            plan = build_reconcile_plan(
                skill_name=str(s.get("tool_name") or ""),
                text=summary,
                status=status,
                structured=structured if isinstance(structured, dict) else None,
            )
            out.append(
                {
                    "step_id": s.get("step_id"),
                    "tool_name": s.get("tool_name"),
                    "status": status,
                    "reconcile": plan,
                }
            )
    return out
