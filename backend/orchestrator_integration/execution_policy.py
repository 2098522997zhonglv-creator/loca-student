"""执行策略：预算归因、批次顺序、完成验证与失败分类（FR01–FR03 / FR02）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestrator_integration.skill_catalog import (
    extract_command_actions,
    resolve_action_side_effect,
)
from orchestrator_integration.task_commission import CHANNEL_HEAVY, CHANNEL_LIGHT

_ATTR_PLANNING = "planning"
_ATTR_TOOL = "tool"
_ATTR_BUSINESS = "business"
_ATTR_CONCURRENCY = "concurrency"

_BUSINESS_RE = re.compile(
    r"余额不足|库存不足|权限不足|无权限|已存在|不存在|状态不允许|业务校验|business[_ ]?error|rejected by policy",
    re.IGNORECASE,
)
_TOOL_RE = re.compile(
    r"timeout|timed out|连接失败|ConnectionError|HTTP\s*[45]\d\d|500|502|503|traceback|Exception|Errno",
    re.IGNORECASE,
)
_CONCURRENCY_RE = re.compile(
    r"ETag|version conflict|乐观锁|并发冲突|stale|被他人修改|409\b",
    re.IGNORECASE,
)
_PLANNING_RE = re.compile(
    r"不在本轮|白名单|辅类|越权|Escape Hatch|亲和组|未登记|needs_confirmation|禁止",
    re.IGNORECASE,
)
_BROWSER_TIMEOUT_RE = re.compile(
    r"timeout|timed out|超时|filechooser|等待.*超时",
    re.IGNORECASE,
)
_BROWSER_SUCCESS_MARKERS = re.compile(
    r"add\s*to\s*cart|加入购物车|cart_id|cart_item|/cart/|购物车|"
    r"1/1|preview|预览刷新|order_sn|订单号",
    re.IGNORECASE,
)
_UPLOAD_COUNT_ONLY_RE = re.compile(r"0/1\s*[→\-~]+\s*1/1|计数.*1/1", re.IGNORECASE)


@dataclass(frozen=True)
class BatchPlan:
    ordered_commands: List[dict]
    parallel: bool
    notes: Tuple[str, ...]


def classify_attribution(
    *,
    error_kind: str = "",
    result_text: str = "",
    http_ok: Optional[bool] = None,
) -> str:
    """偏差归因四象限：planning / tool / business / concurrency。"""
    blob = f"{error_kind}\n{result_text or ''}"
    if error_kind in {
        "allowlist",
        "cross_skill",
        "privilege",
        "unregistered_write",
        "escape_denied",
        "budget_exceeded",
        "batch_plan_reject",
        "needs_confirmation",
        "needs_confirmation_partial",
        "no_commission",
        "postcondition_failed",
        "light_channel_irreversible",
        "nonzero_exit",
        "timeout_unknown",
    }:
        return _ATTR_PLANNING if error_kind not in {
            "nonzero_exit",
            "timeout_unknown",
        } else _ATTR_TOOL
    if _PLANNING_RE.search(blob):
        return _ATTR_PLANNING
    if _CONCURRENCY_RE.search(blob):
        return _ATTR_CONCURRENCY
    if _BUSINESS_RE.search(blob):
        return _ATTR_BUSINESS
    if http_ok is False or _TOOL_RE.search(blob):
        return _ATTR_TOOL
    if error_kind in {"execute_error", "exception", "batch_partial_error"}:
        return _ATTR_TOOL
    return ""


def command_side_effect(command: Optional[str], skill_name: Optional[str] = None) -> str:
    effect = resolve_action_side_effect(skill_name, command)
    if effect:
        return effect
    from orchestrator_integration.builtin_tools.skill_tools import (
        is_mutating_skill_command,
    )

    return "write" if is_mutating_skill_command(command) else "read"


def plan_batch_execution(
    commands: Sequence[dict],
    *,
    want_parallel: bool,
    channel: str = CHANNEL_LIGHT,
) -> Tuple[Optional[str], BatchPlan]:
    """串行批次保留输入顺序（FR02.1）；仅无副作用约束并行，不做全局 read/write 重排。"""
    cmds = [c for c in commands if isinstance(c, dict)]
    if not cmds:
        return "命令列表为空", BatchPlan([], False, ())

    effects = [
        command_side_effect(c.get("command"), c.get("skill_name")) for c in cmds
    ]
    # execute（跑用例/API）有副作用，不可与写操作并行（FR06.1）
    has_write = any(e in ("write", "irreversible", "execute") for e in effects)
    has_irreversible = any(e == "irreversible" for e in effects)
    notes: List[str] = []

    # FR02.1：严格保留输入顺序
    ordered = list(cmds)

    # 不可逆不在末尾且其后仍有其它动作 → 拒绝计划（不做重排）
    if has_irreversible:
        last_irr = max(i for i, e in enumerate(effects) if e == "irreversible")
        if last_irr < len(ordered) - 1:
            return (
                "约束冲突：irreversible 动作必须位于批次末尾；"
                "当前输入顺序不满足，已拒绝该计划（不自动重排）。",
                BatchPlan(ordered, False, ("irreversible_not_last",)),
            )

    if channel == CHANNEL_LIGHT and has_irreversible:
        return (
            "轻量通道禁止 irreversible 动作；请走重量通道/草稿确认。",
            BatchPlan(ordered, False, tuple(notes)),
        )

    parallel = bool(want_parallel) and len(ordered) > 1 and not has_write
    if want_parallel and has_write:
        notes.append("含写入/不可逆，已强制串行（保留输入顺序）")
        parallel = False
    if channel == CHANNEL_HEAVY and parallel:
        notes.append("重量通道默认串行")
        parallel = False
    if not notes:
        notes.append("已按输入顺序执行（未做 read/write 全局重排）")

    return None, BatchPlan(ordered, parallel, tuple(notes))


def light_channel_retry_allowed(attempt: int, channel: str) -> bool:
    """轻量通道：失败最多再试 1 次。"""
    if channel != CHANNEL_LIGHT:
        return False
    return attempt < 1


def resolve_dependent_skips(
    steps: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """依赖失败时跳过后续步骤（FR02.2 / FR02.4）。

    每个 step 形如:
      {step_id, depends_on: [...], on_failure: stop_dependents|continue, status}
    返回带 status=skipped 标注的副本。
    """
    by_id = {str(s.get("step_id") or ""): dict(s) for s in steps if s}
    failed: set = set()
    for sid, step in by_id.items():
        st = str(step.get("status") or "")
        if st in {"failed", "cancelled", "blocked"}:
            failed.add(sid)

    out: List[Dict[str, Any]] = []
    for raw in steps:
        step = dict(raw)
        sid = str(step.get("step_id") or "")
        deps = [str(d) for d in (step.get("depends_on") or []) if d]
        on_fail = str(step.get("on_failure") or "stop_dependents")
        cur = str(step.get("status") or "pending")
        if cur in {"succeeded", "failed", "cancelled", "blocked", "skipped"}:
            out.append(step)
            continue
        blocked_by = [d for d in deps if d in failed]
        if blocked_by and on_fail == "stop_dependents":
            step["status"] = "skipped"
            step["skip_reason"] = f"dependency_failed:{','.join(blocked_by)}"
            failed.add(sid)
        out.append(step)
    return out


def evaluate_browser_checkpoints(joined: str) -> Dict[str, Any]:
    """浏览器上传/加购逐项验收（FR03.5 / FR03.6）。"""
    text = joined or ""
    checks = {
        "has_timeout_or_tool_error": bool(_BROWSER_TIMEOUT_RE.search(text)),
        "has_success_marker": bool(_BROWSER_SUCCESS_MARKERS.search(text)),
        "count_only_1_1": bool(_UPLOAD_COUNT_ONLY_RE.search(text)),
        "has_preview_evidence": bool(
            re.search(r"预览|preview.*(refresh|更新|变化)|素材", text, re.I)
        ),
        "has_cart_evidence": bool(
            re.search(r"cart_id|cart_item|/cart/|加入购物车|add\s*to\s*cart", text, re.I)
        ),
        "execution_method": "",
    }
    from orchestrator_integration.tool_result import infer_execution_method

    checks["execution_method"] = infer_execution_method(text)

    reasons: List[str] = []
    ok = True

    # AC06：仅超时文本不能验收通过
    if checks["has_timeout_or_tool_error"] and not checks["has_success_marker"]:
        ok = False
        reasons.append("浏览器工具超时/失败且无成功业务证据，验收不通过")

    # AC24/AC25：仅 1/1 计数不够；filechooser 失败不算原生选择器成功
    if checks["execution_method"] == "filechooser_failed":
        ok = False
        reasons.append("filechooser 失败：未证明原生文件选择器上传成功")
    if checks["count_only_1_1"] and not checks["has_preview_evidence"]:
        ok = False
        reasons.append("仅计数 1/1 不能证明预览已刷新或素材正确")

    # 有成功标记但无标识类型时不直接失败，由上层交付说明缺口
    return {"ok": ok, "reasons": reasons, "checks": checks}


def evaluate_post_condition(
    *,
    post_condition: str,
    tool_results: Sequence[str],
    had_unconfirmed_write: bool = False,
    playbook_id: str = "",
) -> Dict[str, Any]:
    """确定性后置断言（不靠模型自评）。"""
    joined = "\n".join(str(x) for x in tool_results if x)
    ok = True
    reasons: List[str] = []
    extra: Dict[str, Any] = {}

    if had_unconfirmed_write:
        ok = False
        reasons.append("存在未确认写入，退出断言失败")

    if playbook_id == "url_read":
        if not joined.strip():
            ok = False
            reasons.append("url-reader 无输出")
        elif "needs_confirmation" in joined and "draft" in joined:
            pass
    elif playbook_id == "testcase_execute":
        if "不在本轮分区" in joined or "越权" in joined:
            ok = False
            reasons.append("执行区门禁拒绝")
        if "needs_confirmation" in joined and "create_ui" in joined.lower():
            pass
    elif playbook_id == "platform_write":
        if "needs_confirmation" not in joined and "error" in joined.lower():
            ok = False
            reasons.append("写入路径异常且无确认草稿")
    elif playbook_id == "browser_act":
        browser = evaluate_browser_checkpoints(joined)
        extra["browser_checks"] = browser.get("checks")
        if not browser.get("ok", True):
            ok = False
            reasons.extend(browser.get("reasons") or [])
        elif not joined.strip():
            ok = False
            reasons.append("浏览器任务无工具结果可用于断言")

    if post_condition and not reasons and not joined.strip():
        ok = False
        reasons.append("无工具结果可用于断言")

    # 浏览器：结构化 failed/unknown 且无成功证据 → 不得完成（AC06）
    if playbook_id == "browser_act" and (
        '"status": "failed"' in joined or '"status": "unknown"' in joined
    ) and not _BROWSER_SUCCESS_MARKERS.search(joined):
        ok = False
        if "工具结果含 failed/unknown 且无成功证据" not in reasons:
            reasons.append("工具结果含 failed/unknown 且无成功证据")

    return {
        "ok": ok,
        "post_condition": post_condition,
        "reasons": reasons,
        "playbook_id": playbook_id,
        **extra,
    }
