"""执行循环护栏：超时先核对、同类失败封顶、无进展转阻塞、阶段退出。

对应验收要求：
1. 超时结果先核对（持久会话），未确认前不重做副作用动作
2. 同类失败设重试上限，记录失败原因与恢复条件
3. 工具调用 ≠ 业务进度；无进展超阈值 → blocked
4. fetch_conditions 完成后须转入 check_executable；缺环境时明确阻塞
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from django.utils import timezone

_BROWSER_SKILLS = frozenset(
    {"playwright-skill", "playwright-cli", "browser-use"}
)

# 同类失败（同指纹）连续上限
MAX_IDENTICAL_FAILURES = 2
# 业务进度仍为 0 时，工具调用超过该阈值 → 无进展阻塞
NO_PROGRESS_TOOL_THRESHOLD = 12
# 长期停在 fetch_conditions 烧预算的上限
FETCH_CONDITIONS_STALL_TOOLS = 8

_TIMEOUT_RE = re.compile(r"timeout|timed out|超时", re.I)


def failure_fingerprint(
    *,
    skill_name: str = "",
    command: str = "",
    error_code: str = "",
    summary: str = "",
) -> str:
    """同类失败指纹：skill + 规范化命令片段 + error_code/摘要头。"""
    cmd = " ".join((command or "").split())[:160]
    # 去掉易变的时间戳/随机串，保留选择器与动作语义
    cmd = re.sub(r"\b\d{10,}\b", "<n>", cmd)
    head = " ".join((summary or "").split())[:120]
    raw = f"{(skill_name or '').strip()}|{cmd}|{(error_code or '').strip()}|{head}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _facts(task) -> Dict[str, Any]:
    return dict(getattr(task, "facts", None) or {})


def _save_facts(task, facts: Dict[str, Any]) -> None:
    task.facts = facts
    try:
        type(task).objects.filter(pk=task.pk).update(
            facts=facts, updated_at=timezone.now()
        )
    except Exception:
        pass


def browser_timeout_reconcile_hint(skill_name: str = "") -> str:
    name = (skill_name or "").strip()
    if name not in _BROWSER_SKILLS:
        return ""
    return (
        "超时结果未确认：请用持久会话执行只读核对（page_info / 当前 URL / 截图），"
        "确认页面或操作状态后再决定是否重试；"
        "结果未确认前，禁止重复点击、提交、加购、上传等有副作用动作。"
    )


def enhance_reconcile_for_browser(
    plan: Dict[str, Any],
    *,
    skill_name: str = "",
) -> Dict[str, Any]:
    """在已有核对计划上补充浏览器持久会话指引。"""
    out = dict(plan or {})
    hint = browser_timeout_reconcile_hint(skill_name)
    if not hint:
        return out
    if out.get("must_reconcile"):
        prev = str(out.get("hint") or "")
        if "持久会话" not in prev:
            out["hint"] = f"{prev} {hint}".strip() if prev else hint
        out["reconcile_action"] = out.get("reconcile_action") or "persistent_session_inspect"
        out["allow_replay"] = False
    return out


def note_tool_outcome(
    task,
    *,
    skill_name: str = "",
    command: str = "",
    status: str = "",
    error_code: str = "",
    summary: str = "",
    advanced_stage: bool = False,
) -> Dict[str, Any]:
    """记录工具结果指纹与无进展计数；必要时返回应阻塞原因。"""
    if task is None:
        return {"ok": True}
    facts = _facts(task)
    st = (status or "").lower()
    code = (error_code or "").strip()
    is_fail = st in {"failed", "unknown", "error", "blocked"} or bool(code)
    fp = failure_fingerprint(
        skill_name=skill_name,
        command=command,
        error_code=code or st,
        summary=summary,
    )

    # 业务阶段推进则清零无进展与同类失败 streak
    if advanced_stage:
        facts["identical_failure"] = {}
        facts["tools_since_business_progress"] = 0
        facts.pop("pending_timeout_reconcile", None)
        _save_facts(task, facts)
        return {"ok": True, "cleared": True}

    tools_since = int(facts.get("tools_since_business_progress") or 0) + 1
    facts["tools_since_business_progress"] = tools_since

    budget = dict(getattr(task, "budget", None) or {})
    biz_done = int(budget.get("completed_business_steps") or 0)

    if is_fail:
        prev = dict(facts.get("identical_failure") or {})
        if prev.get("fingerprint") == fp:
            streak = int(prev.get("streak") or 0) + 1
        else:
            streak = 1
        facts["identical_failure"] = {
            "fingerprint": fp,
            "streak": streak,
            "skill_name": skill_name,
            "error_code": code or st,
            "summary": (summary or "")[:240],
            "updated_at": timezone.now().isoformat(),
        }
        # 超时/未知：标记必须先核对
        if st == "unknown" or code == "timeout_unknown" or _TIMEOUT_RE.search(
            summary or ""
        ):
            facts["pending_timeout_reconcile"] = {
                "skill_name": skill_name,
                "fingerprint": fp,
                "hint": browser_timeout_reconcile_hint(skill_name)
                or "超时结果先核对，禁止盲目重放副作用动作。",
            }
        _save_facts(task, facts)
        if streak >= MAX_IDENTICAL_FAILURES:
            return {
                "ok": False,
                "block": True,
                "blocker_code": "identical_failure_cap",
                "reasons": [
                    f"同类失败已连续 {streak} 次（上限 {MAX_IDENTICAL_FAILURES}）："
                    f"skill={skill_name or '?'} code={code or st}",
                    (summary or "")[:200],
                ],
                "recovery": (
                    "停止当前循环并记录失败原因；恢复条件："
                    "先核对超时/选择器/环境后更换策略或缩小目标，"
                    "再发送「继续」。禁止对同一失败原样重试。"
                ),
            }
    else:
        # 成功/跳过：打断同类失败链；副作用成功才算业务进展由阶段推进负责
        facts["identical_failure"] = {}
        if st in {"succeeded", "skipped"}:
            facts.pop("pending_timeout_reconcile", None)
        _save_facts(task, facts)

    # 无业务进展烧工具
    if biz_done <= 0 and tools_since >= NO_PROGRESS_TOOL_THRESHOLD:
        return {
            "ok": False,
            "block": True,
            "blocker_code": "no_business_progress_threshold",
            "reasons": [
                f"工具已调用 {tools_since} 次仍无业务检查点进展"
                f"（阈值 {NO_PROGRESS_TOOL_THRESHOLD}）；工具调用不等于业务进度。"
            ],
            "recovery": (
                "取得当前检查点合格证据后再继续；"
                "若缺环境/执行器请先修复，勿在准备阶段空转烧预算。"
            ),
        }

    stage = str(getattr(task, "current_stage", "") or "")
    if stage == "fetch_conditions" and tools_since >= FETCH_CONDITIONS_STALL_TOOLS:
        return {
            "ok": False,
            "block": True,
            "blocker_code": "fetch_conditions_stall",
            "reasons": [
                f"长期停在 fetch_conditions（工具 {tools_since} 次），"
                "条件检索完成后必须转入 check_executable；"
                "禁止在本阶段反复消耗预算。"
            ],
            "recovery": (
                "若入口/规则已具备：推进到 check_executable 做可执行性检查；"
                "若缺环境或工具：明确阻塞并列出缺口，勿继续 knowledge_search 空转。"
            ),
        }

    return {"ok": True, "tools_since_business_progress": tools_since, "fingerprint": fp}


def preflight_side_effect_after_timeout(
    task,
    *,
    skill_name: str = "",
    command: str = "",
) -> Dict[str, Any]:
    """执行前：若有未核超时，禁止重做可能副作用的动作。"""
    if task is None:
        return {"ok": True}
    facts = _facts(task)
    pending = facts.get("pending_timeout_reconcile")
    if not isinstance(pending, dict) or not pending:
        return {"ok": True}

    from orchestrator_integration.resume_execution import is_side_effect_command

    if not is_side_effect_command(skill_name or "", command or ""):
        # 只读核对（page_info 等）放行，并可清 pending 由调用方在成功后处理
        return {
            "ok": True,
            "reconcile_read_allowed": True,
            "pending": pending,
        }

    hint = str(pending.get("hint") or "") or browser_timeout_reconcile_hint(
        skill_name
    )
    return {
        "ok": False,
        "block_execute": True,
        "blocker_code": "timeout_must_reconcile_first",
        "message": (
            "超时结果尚未核对，禁止重复执行可能产生副作用的动作。"
            + (f" {hint}" if hint else "")
        ),
        "recovery": hint
        or "用持久会话只读检查页面状态；确认失败后再有界重试。",
        "pending": pending,
    }


def clear_timeout_reconcile_if_inspect(task, *, command: str = "") -> None:
    """只读核对命令成功后可清除 pending（由调用方在 succeeded 时调用）。"""
    if task is None:
        return
    blob = (command or "").lower()
    inspect_markers = (
        "page_info",
        "page.url",
        "screenshot",
        "current_url",
        "wait_for_load",
        "content()",
        "title()",
    )
    if not any(m in blob for m in inspect_markers):
        return
    facts = _facts(task)
    if facts.pop("pending_timeout_reconcile", None) is not None:
        _save_facts(task, facts)


def mark_business_progress(task) -> None:
    """业务检查点推进时清零无进展计数。"""
    if task is None:
        return
    facts = _facts(task)
    facts["tools_since_business_progress"] = 0
    facts["identical_failure"] = {}
    facts["progress_phase"] = "business_exec"
    _save_facts(task, facts)


def evaluate_stage_exit_fetch_conditions(
    task,
    *,
    tool_results: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """fetch_conditions 已具备入口证据时，强制建议推进到 check_executable。"""
    if task is None:
        return {"ok": True}
    stage = str(getattr(task, "current_stage", "") or "")
    if stage != "fetch_conditions":
        return {"ok": True, "reason": "not_fetch_stage"}

    from orchestrator_integration.task_orchestrator.node_runtime import (
        orch_node_acceptance,
    )

    verdict = orch_node_acceptance("fetch_conditions", tool_results or [])
    if not verdict.get("ok"):
        # 缺环境：探测并给出阻塞建议
        facts = _facts(task)
        tools_since = int(facts.get("tools_since_business_progress") or 0)
        if tools_since >= FETCH_CONDITIONS_STALL_TOOLS:
            return {
                "ok": False,
                "block": True,
                "blocker_code": "fetch_conditions_stall",
                "reasons": [
                    "fetch_conditions 未取得可推进证据且持续消耗工具预算。",
                    str(verdict.get("reason") or "stage_not_accepted"),
                ],
                "recovery": (
                    "补齐入口 URL/规则后推进；缺执行器或 Skill 时报告环境缺口并 blocked。"
                ),
                "verdict": verdict,
            }
        return {"ok": True, "advance": False, "verdict": verdict}

    return {
        "ok": True,
        "advance": True,
        "verdict": verdict,
        "next_stage": "check_executable",
        "message": (
            "条件检索已具备入口证据，必须转入 check_executable 做可执行性检查；"
            "勿继续在 fetch_conditions 消耗预算。"
        ),
    }


def apply_loop_block(task, decision: Dict[str, Any]) -> Dict[str, Any]:
    """将护栏决策落为任务 blocked。"""
    if not decision or not (decision.get("block") or decision.get("block_execute")):
        return {"action": "none"}
    from orchestrator_integration.task_state_closure import _block_task

    return _block_task(
        task,
        blocker_code=str(
            decision.get("blocker_code") or "execution_loop_guard"
        ),
        reasons=list(decision.get("reasons") or [decision.get("message") or "执行循环护栏触发"]),
        recovery=str(
            decision.get("recovery")
            or "核对超时/环境后发送「继续」；禁止盲目重试同一失败。"
        ),
        acceptance={
            "blockers": [
                {
                    "blocker_code": decision.get("blocker_code"),
                    "step": str(getattr(task, "current_stage", "") or "execution"),
                }
            ]
        },
    )
