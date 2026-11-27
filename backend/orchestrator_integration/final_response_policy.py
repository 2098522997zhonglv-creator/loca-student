"""Deterministic guard for assistant replies that defer execution to the user."""
from __future__ import annotations

import re


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


def should_continue_incomplete_task(
    *,
    executor_mounted: bool,
    user_stopped: bool,
    interrupt_detected: bool,
    task_status: str,
    used_tool_actions: int,
    reserved_tool_actions: int,
    max_tool_actions: int,
    completed_business_steps: int,
    expected_business_steps: int,
) -> bool:
    """Continue a still-running task when persisted business work remains."""
    has_budget = (
        max_tool_actions > 0
        and used_tool_actions + reserved_tool_actions < max_tool_actions
    )
    has_work = (
        expected_business_steps > 0
        and completed_business_steps < expected_business_steps
    )
    return bool(
        not user_stopped
        and not interrupt_detected
        and executor_mounted
        and task_status == "running"
        and has_budget
        and has_work
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
