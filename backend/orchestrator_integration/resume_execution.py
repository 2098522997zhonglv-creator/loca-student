"""第 2 阶段：续跑与幂等。

恢复时先读步骤记录：已完成跳过；未知结果先核对；副作用同键禁止盲目重放。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from orchestrator_integration.idempotency import (
    build_idempotency_key,
    should_inject_idempotency,
)
from orchestrator_integration.reconcile import (
    build_reconcile_plan,
    collect_uncertain_steps,
    is_uncertain_result,
)


_SIDE_EFFECT_MARKERS = (
    "add_to_cart",
    "submit_order",
    "checkout",
    "pay",
    "create_",
    "update_",
    "delete_",
    "publish",
    "upload",
    "write",
    "set_input_files",
    "execute_testcase",
    "run_testcase",
)


def _step_idempotency_key(step: Dict[str, Any]) -> str:
    evidence = step.get("evidence") if isinstance(step.get("evidence"), dict) else {}
    structured = (
        step.get("structured_result")
        if isinstance(step.get("structured_result"), dict)
        else {}
    )
    key = (
        (evidence or {}).get("idempotency_key")
        or structured.get("idempotency_key")
        or ""
    )
    return str(key or "")


def load_task_steps(task) -> List[Dict[str, Any]]:
    from orchestrator_integration.models import AgentStep

    rows = (
        AgentStep.objects.filter(task=task)
        .order_by("step_number")
        .values(
            "id",
            "step_number",
            "step_id",
            "stage_id",
            "status",
            "tool_name",
            "tool_input",
            "tool_output_summary",
            "param_fingerprint",
            "operation_id",
            "structured_result",
            "evidence",
            "attempt_count",
        )
    )
    return [dict(r) for r in rows]


def is_side_effect_command(skill_name: str = "", command: str = "") -> bool:
    if not should_inject_idempotency(skill_name or "", command or ""):
        # should_inject false means read-like → not side effect for replay guard
        blob = (command or "").lower()
        if any(m in blob for m in _SIDE_EFFECT_MARKERS):
            return True
        return False
    return True


def build_resume_plan(task) -> Dict[str, Any]:
    """根据已保存步骤生成续跑计划。"""
    steps = load_task_steps(task)
    completed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    uncertain: List[Dict[str, Any]] = []
    open_steps: List[Dict[str, Any]] = []
    side_effect_keys: List[str] = []

    for s in steps:
        st = str(s.get("status") or "")
        item = {
            "step_number": s.get("step_number"),
            "step_id": s.get("step_id"),
            "stage_id": s.get("stage_id"),
            "tool_name": s.get("tool_name"),
            "status": st,
            "param_fingerprint": s.get("param_fingerprint") or "",
            "operation_id": s.get("operation_id") or "",
            "idempotency_key": _step_idempotency_key(s),
            "input_summary": _input_summary(s.get("tool_input")),
            "output_summary": (s.get("tool_output_summary") or "")[:240],
        }
        if st in {"succeeded", "skipped"}:
            if st == "succeeded":
                completed.append(item)
            else:
                skipped.append(item)
            key = item["idempotency_key"]
            if key and is_side_effect_command(
                str(s.get("tool_name") or ""),
                _command_from_input(s.get("tool_input")),
            ):
                side_effect_keys.append(key)
        elif st == "failed":
            failed.append(item)
        elif st == "unknown" or is_uncertain_result(
            str(s.get("tool_output_summary") or ""), st
        ):
            plan = build_reconcile_plan(
                skill_name=str(s.get("tool_name") or ""),
                text=str(s.get("tool_output_summary") or ""),
                status=st or "unknown",
                structured=s.get("structured_result")
                if isinstance(s.get("structured_result"), dict)
                else None,
            )
            item["reconcile"] = plan
            uncertain.append(item)
        elif st in {"pending", "running", "awaiting_approval"}:
            open_steps.append(item)

    # 阶段完成情况
    facts = dict(getattr(task, "facts", None) or {})
    done_stages = [
        str(x)
        for x in (facts.get("completed_stage_ids") or [])
        if not isinstance(x, dict)
    ]
    stages = [
        str(s.get("stage_id") or "")
        for s in (getattr(task, "stage_plan", None) or [])
        if isinstance(s, dict) and s.get("stage_id")
    ]
    remaining_stages = [sid for sid in stages if sid and sid not in set(done_stages)]

    must_reconcile = bool(uncertain)
    return {
        "task_id": getattr(task, "public_task_id", None) or str(getattr(task, "pk", "")),
        "must_reconcile": must_reconcile,
        "completed_steps": completed,
        "skipped_steps": skipped,
        "failed_steps": failed,
        "uncertain_steps": uncertain,
        "open_steps": open_steps,
        "side_effect_idempotency_keys": sorted(set(side_effect_keys)),
        "completed_stage_ids": done_stages,
        "remaining_stages": remaining_stages,
        "hint": format_resume_plan_hint(
            {
                "must_reconcile": must_reconcile,
                "completed_steps": completed,
                "uncertain_steps": uncertain,
                "remaining_stages": remaining_stages,
                "side_effect_idempotency_keys": side_effect_keys,
            }
        ),
    }


def format_resume_plan_hint(plan: Dict[str, Any]) -> str:
    lines = ["【续跑计划·幂等】"]
    completed = plan.get("completed_steps") or []
    if completed:
        lines.append(
            f"- 已完成 {len(completed)} 步：跳过，勿重复有副作用动作"
            f"（operation_id="
            + ",".join(
                str(s.get("operation_id") or s.get("step_id") or "")
                for s in completed[:6]
            )
            + "）。"
        )
    uncertain = plan.get("uncertain_steps") or []
    if uncertain:
        lines.append(
            "- 存在结果未知步骤：先核对原动作状态（查询记录/页面/接口），"
            "禁止盲目重放同一写入。"
        )
        for u in uncertain[:3]:
            rec = u.get("reconcile") or {}
            lines.append(
                f"  · step={u.get('step_id')} tool={u.get('tool_name')} "
                f"→ {rec.get('reconcile_action') or 'query_original_record'}"
            )
    remaining = plan.get("remaining_stages") or []
    if remaining:
        lines.append("- 未完成阶段：" + " → ".join(remaining[:8]))
    keys = plan.get("side_effect_idempotency_keys") or []
    if keys:
        lines.append(
            f"- 已成功副作用幂等键 {len(set(keys))} 个：相同键再次调用应跳过/返回原证据。"
        )
    if len(lines) == 1:
        lines.append("- 无已保存步骤；按当前阶段正常执行。")
    return "\n".join(lines)


def _command_from_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        return str(
            tool_input.get("command")
            or tool_input.get("script")
            or tool_input.get("raw")
            or ""
        )
    return str(tool_input or "")


def _input_summary(tool_input: Any, limit: int = 240) -> str:
    if tool_input is None:
        return ""
    if isinstance(tool_input, dict):
        blob = {
            k: tool_input.get(k)
            for k in ("skill_name", "command", "script", "action", "testcase_id")
            if k in tool_input
        } or tool_input
        text = str(blob)
    else:
        text = str(tool_input)
    text = " ".join(text.split())
    return text[:limit]


def find_succeeded_side_effect(
    task,
    *,
    skill_name: str = "",
    command: str = "",
    idempotency_key: str = "",
) -> Optional[Dict[str, Any]]:
    """若相同幂等键（或指纹）已成功，返回原步骤摘要供跳过重放。"""
    key = idempotency_key or ""
    if not key and skill_name:
        key = build_idempotency_key(
            task_id=str(
                getattr(task, "public_task_id", None) or getattr(task, "pk", "") or ""
            ),
            skill_name=skill_name,
            command=command or "",
        )
    if not key:
        return None
    for s in load_task_steps(task):
        if str(s.get("status") or "") != "succeeded":
            continue
        if _step_idempotency_key(s) == key:
            return {
                "skip": True,
                "reason": "idempotent_replay_blocked",
                "idempotency_key": key,
                "step_id": s.get("step_id"),
                "operation_id": s.get("operation_id"),
                "output_summary": (s.get("tool_output_summary") or "")[:500],
                "structured_result": s.get("structured_result") or {},
                "evidence": s.get("evidence") or {},
            }
    return None


def guard_side_effect_replay(
    task,
    *,
    skill_name: str = "",
    command: str = "",
) -> Dict[str, Any]:
    """执行前守卫：副作用且同键已成功 → 跳过；同键未知 → 禁止重放并要求核对。"""
    if task is None:
        return {"skip": False}
    if not is_side_effect_command(skill_name, command):
        return {"skip": False, "reason": "not_side_effect"}
    hit = find_succeeded_side_effect(
        task, skill_name=skill_name, command=command
    )
    if hit:
        return hit
    # 同键未知：不得盲目重试
    key = build_idempotency_key(
        task_id=str(
            getattr(task, "public_task_id", None) or getattr(task, "pk", "") or ""
        ),
        skill_name=skill_name or "",
        command=command or "",
    )
    for s in load_task_steps(task):
        st = str(s.get("status") or "")
        if st != "unknown" and not is_uncertain_result(
            str(s.get("tool_output_summary") or ""), st
        ):
            continue
        if _step_idempotency_key(s) == key or (
            not _step_idempotency_key(s)
            and fingerprint_match(s, skill_name, command)
        ):
            plan = build_reconcile_plan(
                skill_name=skill_name or str(s.get("tool_name") or ""),
                text=str(s.get("tool_output_summary") or ""),
                status="unknown",
                structured=s.get("structured_result")
                if isinstance(s.get("structured_result"), dict)
                else None,
            )
            return {
                "skip": True,
                "reason": "uncertain_must_reconcile",
                "must_reconcile": True,
                "idempotency_key": key,
                "step_id": s.get("step_id"),
                "operation_id": s.get("operation_id"),
                "reconcile": plan,
                "output_summary": (s.get("tool_output_summary") or "")[:500],
                "structured_result": {
                    "status": "unknown",
                    "reason": "uncertain_must_reconcile",
                    "reconcile": plan,
                },
                "evidence": s.get("evidence") or {},
            }
    return {"skip": False, "reason": "no_prior_success"}


def fingerprint_match(step: Dict[str, Any], skill_name: str, command: str) -> bool:
    """无幂等键时，用工具名+命令指纹近似匹配。"""
    from orchestrator_integration.agent_runtime import fingerprint_payload

    fp = str(step.get("param_fingerprint") or "")
    if not fp:
        return False
    candidate = fingerprint_payload(
        {"skill_name": skill_name, "command": command}
    )
    # 也比对仅 command / tool_input 形态
    alt = fingerprint_payload({"command": command})
    ti = step.get("tool_input")
    if isinstance(ti, dict):
        step_cmd = _command_from_input(ti)
        if step_cmd and step_cmd.strip() == (command or "").strip():
            return True
    return fp in {candidate, alt}


def enrich_structured_with_side_effect_state(
    structured: Optional[Dict[str, Any]],
    *,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    idempotency_key: str = "",
) -> Dict[str, Any]:
    """写入调用前后状态，供未知结果核对。"""
    data = dict(structured or {})
    evidence = dict(data.get("evidence") or {})
    if before is not None:
        evidence["side_effect_before"] = before
    if after is not None:
        evidence["side_effect_after"] = after
    if idempotency_key:
        evidence["idempotency_key"] = idempotency_key
        data["idempotency_key"] = idempotency_key
    data["evidence"] = evidence
    return data


def resume_preflight_v2(task) -> Dict[str, Any]:
    """扩展 resume_preflight：附带完整续跑计划。"""
    plan = build_resume_plan(task)
    uncertain = collect_uncertain_steps(load_task_steps(task))
    return {
        "must_reconcile": bool(plan.get("must_reconcile") or uncertain),
        "uncertain_steps": plan.get("uncertain_steps") or uncertain,
        "resume_plan": plan,
        "hint": plan.get("hint") or "",
    }
