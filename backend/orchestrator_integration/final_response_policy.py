"""Deterministic guard for assistant replies that defer execution to the user."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Sequence


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

_UNVERIFIED_BUSINESS_CLAIM = re.compile(
    r"(?:商品检查(?:已)?完成|已完成商品检查|检查点(?:已)?(?:全部)?完成|"
    r"回归(?:已)?完成|已完成回归|已验证(?:全部|所有)?(?:商品|检查点|页面)|"
    r"本轮已验证|已核对(?:全部|所有)?商品|"
    r"(?:矩阵|缺陷列表)(?:已)?交付)",
    re.I,
)

_HISTORICAL_EVIDENCE_DIR = re.compile(
    r"reg_std_\w+|上一轮|上一次|历史截图|历史证据",
    re.I,
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


def claims_unverified_business_completion(text: str) -> bool:
    """终态答复含未绑定本轮证据的验证性/完成性表述。"""
    text = (text or "").strip()
    if not text:
        return False
    if _UNVERIFIED_BUSINESS_CLAIM.search(text):
        return True
    if looks_like_early_final_answer(text) and _HISTORICAL_EVIDENCE_DIR.search(text):
        return True
    return False


def should_correct_unverified_delivery(
    *,
    response_text: str,
    acceptance: Optional[Dict[str, Any]] = None,
    completed_business_steps: int = 0,
    expected_business_steps: int = 0,
) -> bool:
    """证据门禁失败或业务未完成时，剥离未关联本轮证据的验证性终态表述。"""
    text = (response_text or "").strip()
    if not text:
        return False
    acc = acceptance or {}
    blockers = acc.get("blockers") or []
    codes = {
        str((b or {}).get("blocker_code") or "")
        for b in blockers
        if isinstance(b, dict)
    }
    reasons = "；".join(str(r) for r in (acc.get("reasons") or []))
    evidence_gap = (
        (acc.get("evidence_gate") or {}).get("ok") is False
        or "verified_without_evidence" in codes
        or "missing_evidence" in codes
        or "声明已验证但缺少可追溯证据引用" in reasons
    )
    incomplete_biz = expected_business_steps > 0 and (
        completed_business_steps < expected_business_steps
    )
    zero_biz_blocked = (
        expected_business_steps <= 0
        and completed_business_steps <= 0
        and acc.get("ok") is False
    )
    if not (evidence_gap or incomplete_biz or zero_biz_blocked):
        return False
    if claims_unverified_business_completion(text):
        return True
    if evidence_gap and looks_like_early_final_answer(text):
        return True
    if evidence_gap and _HISTORICAL_EVIDENCE_DIR.search(text):
        return True
    return False


def correction_for_unverified_delivery(
    *,
    acceptance: Optional[Dict[str, Any]] = None,
    completed_business_steps: int = 0,
    expected_business_steps: int = 0,
    executor_mounted: bool = True,
    budget_remaining: bool = True,
) -> str:
    acc = acceptance or {}
    reasons = [str(r) for r in (acc.get("reasons") or []) if r]
    reason_line = ("；".join(reasons[:3])) if reasons else "缺少可追溯的本轮证据引用"
    biz = (
        f"{completed_business_steps}/{expected_business_steps}"
        if expected_business_steps
        else f"{completed_business_steps}/—"
    )
    if executor_mounted and budget_remaining:
        next_step = (
            "服务端将在预算内自动续跑采集本轮页面/截图/工具证据；"
            "禁止要求用户代为提供执行证据或手动重挂工具。"
        )
    elif not executor_mounted:
        next_step = (
            "当前缺少已绑定的执行工具，属工具挂载阻塞；"
            "由服务端恢复挂载后继续，禁止要求用户替 Agent 提供执行证据。"
        )
    else:
        next_step = (
            "工具预算已耗尽且仍缺本轮证据；缩小目标或提高预算后由服务端续跑，"
            "禁止要求用户代为提供执行证据。"
        )
    return (
        "更正：本轮不能据此声称商品检查/业务验收已完成。"
        f"业务进度 {biz}；{reason_line}。"
        "历史截图目录或上一轮产物不得当作本轮验证证据。"
        f"任务保持未完成；{next_step}"
    )


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
    missing_evidence: bool = False,
) -> bool:
    """检查点未齐 + 有预算 + 执行工具已挂载时，做一次受控同轮续跑。

    触发条件（满足其一即可）：
    - 模型提前终答 / 假称验收通过
    - 业务进度仍为 0（有检查点却未推进）
    - 证据门禁缺本轮证据（缺证据应继续执行，勿甩给用户补证）
    """
    has_budget = (
        max_tool_actions > 0
        and used_tool_actions + reserved_tool_actions < max_tool_actions
    )
    has_work = (
        expected_business_steps > 0
        and completed_business_steps < expected_business_steps
    )
    stalled_zero = (
        expected_business_steps > 0 and completed_business_steps <= 0
    )
    early = looks_like_early_final_answer(response_text) or acceptance_ok is True
    needs_evidence_run = bool(missing_evidence) or claims_unverified_business_completion(
        response_text
    )
    trigger = early or stalled_zero or needs_evidence_run
    return bool(
        not user_stopped
        and not interrupt_detected
        and executor_mounted
        and has_budget
        and has_work
        and trigger
        and not requests_user_to_remount_executor(response_text)
    )


def recovery_for_missing_evidence(
    *,
    executor_mounted: bool,
    budget_remaining: bool,
) -> str:
    """缺证据时的恢复文案：有工具+预算 → 服务端续跑；缺挂载 → 挂载阻塞。"""
    if not executor_mounted:
        return (
            "工具挂载阻塞：服务端将恢复绑定 execute_skill_script 后继续采集证据；"
            "禁止要求用户替 Agent 提供执行证据或手动重挂。"
        )
    if budget_remaining:
        return (
            "执行工具可用且预算充足：服务端将自动续跑采集本轮页面/截图/工具证据；"
            "禁止要求用户代为提供执行证据。"
        )
    return (
        "工具预算已耗尽且仍缺本轮证据；提高预算或缩小目标后由服务端续跑，"
        "禁止要求用户代为提供执行证据。"
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
