"""Deterministic guard for assistant replies that defer execution to the user."""
from __future__ import annotations

import re
from typing import Optional


_EXECUTOR_REMOUNT_REQUEST = re.compile(
    r"(?:需要你|请你|麻烦你|请先).{0,32}(?:重新)?挂载.{0,32}"
    r"(?:执行类工具|执行工具|浏览器工具|execute_skill_script|playwright)"
    r"|重新挂载.{0,24}(?:执行类工具|执行工具|浏览器工具|execute_skill_script|playwright)"
    r"|工具恢复后.{0,40}(?:我|再|将).{0,20}(?:执行|跑|继续)",
    re.I | re.S,
)

_USER_TRIGGER_DEFERRAL = re.compile(
    r"(?:需要你|请你|麻烦你|请).{0,24}(?:触发|重发|再发|回复|回一句|重新下发)"
    r".{0,50}(?:任务|目标|继续|执行|跑|回归|工具)"
    r"|(?:再发|重新发|回复|回一句|发一句).{0,50}"
    r"(?:就会|即可|之后|后).{0,40}(?:执行|跑|自动|切回|继续)"
    r"|(?:请回|回复).{0,20}(?:继续执行|继续跑|继续回归)",
    re.I | re.S,
)


def requests_user_to_remount_executor(text: str) -> bool:
    text = text or ""
    return bool(
        _EXECUTOR_REMOUNT_REQUEST.search(text)
        or _USER_TRIGGER_DEFERRAL.search(text)
    )


_EARLY_FINAL_CLAIM = re.compile(
    r"(?:任务(?:已)?完成|已全部完成|全部检查点|交付完成|验收通过|"
    r"最终结果|最终回复|到此结束|无需再执行|可以结束了|"
    r"completed successfully|all (?:done|complete)|mission accomplished)",
    re.I,
)


def looks_like_early_final_answer(text: str) -> bool:
    """模型在业务未完成时给出终答/交付口吻。"""
    text = (text or "").strip()
    if not text:
        return False
    if requests_user_to_remount_executor(text):
        return False
    return bool(_EARLY_FINAL_CLAIM.search(text))


def should_attempt_executor_recovery(
    *,
    response_text: str,
    executor_mounted: bool,
    user_stopped: bool,
    interrupt_detected: bool,
    used_tool_actions: int,
    reserved_tool_actions: int,
    max_tool_actions: int,
    completed_business_steps: int,
    expected_business_steps: int,
) -> bool:
    """Allow one bounded continuation for a stale executor-unavailable answer."""
    has_budget = (
        max_tool_actions > 0
        and used_tool_actions + reserved_tool_actions < max_tool_actions
    )
    has_work = (
        expected_business_steps <= 0
        or completed_business_steps < expected_business_steps
    )
    return bool(
        not user_stopped
        and not interrupt_detected
        and executor_mounted
        and requests_user_to_remount_executor(response_text)
        and has_budget
        and has_work
    )


def should_attempt_checkpoint_continue(
    *,
    response_text: str,
    executor_mounted: bool,
    user_stopped: bool,
    interrupt_detected: bool,
    used_tool_actions: int,
    reserved_tool_actions: int,
    max_tool_actions: int,
    completed_business_steps: int,
    expected_business_steps: int,
    acceptance_ok: Optional[bool] = None,
) -> bool:
    """第 1 阶段：检查点未齐 + 有预算时，对提前终答做一次受控同轮续跑。

    与 remount 续跑互补：覆盖「完成第 1 步后模型提前结束」受控场景。
    """
    has_budget = (
        max_tool_actions > 0
        and used_tool_actions + reserved_tool_actions < max_tool_actions
    )
    has_work = (
        expected_business_steps > 0
        and completed_business_steps < expected_business_steps
    )
    early = looks_like_early_final_answer(response_text) or acceptance_ok is True
    return bool(
        not user_stopped
        and not interrupt_detected
        and executor_mounted
        and has_budget
        and has_work
        and early
        and not requests_user_to_remount_executor(response_text)
    )


def correction_for_executor_deferral(*, executor_mounted: bool) -> str:
    if executor_mounted:
        return (
            "更正：本轮执行工具已挂载，但没有完成任何业务执行。任务保持未完成；"
            "我不应要求你重新挂载工具或重发任务。"
        )
    return (
        "更正：本轮没有可用的执行工具，也没有完成业务执行。任务保持未完成；"
        "我不应要求你重新挂载工具或重发任务，需由服务端恢复执行能力后继续。"
    )
