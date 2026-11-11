"""执行策略：三级通道、步数预算、Fork-Join、不可逆置末、归因与后置断言。"""

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
    }:
        return _ATTR_PLANNING
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
    """Fork-Join：只读可并行；含写强制串行；irreversible 必须置末。"""
    cmds = [c for c in commands if isinstance(c, dict)]
    if not cmds:
        return "命令列表为空", BatchPlan([], False, ())

    effects = [
        command_side_effect(c.get("command"), c.get("skill_name")) for c in cmds
    ]
    has_write = any(e in ("write", "irreversible") for e in effects)

    notes: List[str] = []
    # Fork-Join 串行序：read → write → irreversible（同类保持相对顺序）
    reads: List[dict] = []
    writes: List[dict] = []
    irreversible: List[dict] = []
    for c, e in zip(cmds, effects):
        if e == "irreversible":
            irreversible.append(c)
        elif e == "write":
            writes.append(c)
        else:
            reads.append(c)
    ordered = reads + writes + irreversible
    if ordered != list(cmds):
        if irreversible:
            notes.append("已按 read→write→irreversible 重排批次")
        elif writes and reads:
            notes.append("已将只读动作提前、写入串行置后")

    if channel == CHANNEL_LIGHT and irreversible:
        return (
            "轻量通道禁止 irreversible 动作；请走重量通道/草稿确认。",
            BatchPlan(ordered, False, tuple(notes)),
        )

    parallel = bool(want_parallel) and len(ordered) > 1 and not has_write
    if want_parallel and has_write:
        notes.append("含写入/不可逆，已强制串行（Fork-Join）")
        parallel = False
    if channel == CHANNEL_HEAVY and parallel:
        notes.append("重量通道默认串行")
        parallel = False

    return None, BatchPlan(ordered, parallel, tuple(notes))


def light_channel_retry_allowed(attempt: int, channel: str) -> bool:
    """轻量通道：失败最多再试 1 次。"""
    if channel != CHANNEL_LIGHT:
        return False
    return attempt < 1


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
            # 只读执行已跑、写入草稿挂起 → 仍可算主目标完成
            pass
    elif playbook_id == "platform_write":
        if "needs_confirmation" not in joined and "error" in joined.lower():
            ok = False
            reasons.append("写入路径异常且无确认草稿")

    if post_condition and not reasons and not joined.strip():
        ok = False
        reasons.append("无工具结果可用于断言")

    return {
        "ok": ok,
        "post_condition": post_condition,
        "reasons": reasons,
        "playbook_id": playbook_id,
    }
