"""统一 Agent 运行时辅助层（FR04/FR07/FR10）。

HTTP 入口仍在 agent_loop_view；本模块负责任务持久化、租约、预算、事件与验收收尾。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = {
    "max_tool_steps": 28,
    "used_tool_steps": 0,
    "max_retries": 3,
    "used_retries": 0,
    "max_tokens": 0,
    "used_tokens": 0,
}


def _new_public_id() -> str:
    return uuid.uuid4().hex[:16]


def fingerprint_payload(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def create_or_resume_task(
    *,
    session,
    user,
    project_id: Optional[int],
    goal: str,
    commission,
    auth_scope: Optional[Dict[str, Any]] = None,
    stage_plan: Optional[List[Dict[str, Any]]] = None,
    lease_owner: str = "",
) -> Any:
    """创建新任务或复用同会话未完成任务（继续语义）。"""
    from orchestrator_integration.models import AgentTask, AgentBlackboard

    owner = lease_owner or f"worker-{uuid.uuid4().hex[:8]}"
    now = timezone.now()
    open_statuses = (
        "pending",
        "running",
        "awaiting_approval",
        "blocked",
        "paused",
        "cancelling",
    )
    existing = (
        AgentTask.objects.filter(session=session, status__in=open_statuses)
        .order_by("-updated_at")
        .first()
    )
    # 「继续」优先挂到未完成任务
    continue_like = any(
        k in (goal or "") for k in ("继续", "接着", "剩下的", "复用 session_id")
    )
    if existing and continue_like:
        task = existing
        if not acquire_lease(task, owner):
            return task
        task.goal = goal or task.goal
        if auth_scope:
            merged = dict(task.auth_scope or {})
            merged.update(auth_scope)
            task.auth_scope = merged
        task.save(
            update_fields=["goal", "auth_scope", "updated_at", "lease_owner", "lease_expires_at", "status"]
        )
        return task

    budget = dict(DEFAULT_BUDGET)
    if commission is not None:
        budget["max_tool_steps"] = int(getattr(commission, "max_tool_steps", 28) or 28)
    public_id = getattr(commission, "task_id", None) or _new_public_id()
    stages = stage_plan or []
    current_stage = stages[0]["stage_id"] if stages else ""
    task = AgentTask.objects.create(
        session=session,
        project_id=project_id,
        user=user,
        public_task_id=public_id,
        goal=goal or "",
        status="running",
        max_steps=budget["max_tool_steps"],
        plan_version=1,
        current_stage=current_stage,
        stage_plan=stages,
        auth_scope=auth_scope or {},
        task_scope={
            "target_url": _extract_url(goal),
            "raw_goal": (goal or "")[:2000],
        },
        budget=budget,
        facts={},
        lease_owner=owner,
        lease_expires_at=now + timedelta(minutes=15),
        version=1,
    )
    AgentBlackboard.objects.get_or_create(task=task)
    append_event(
        task,
        "task_started",
        status="running",
        payload={"goal": task.goal[:500], "stage": current_stage},
    )
    return task


def acquire_lease(task, owner: str, ttl_minutes: int = 15) -> bool:
    """条件更新获取执行权（FR04.3）。"""
    from orchestrator_integration.models import AgentTask

    now = timezone.now()
    expires = now + timedelta(minutes=ttl_minutes)
    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return False
        lease_ok = (
            not row.lease_owner
            or row.lease_owner == owner
            or (row.lease_expires_at and row.lease_expires_at <= now)
        )
        if not lease_ok and row.status == "running":
            return False
        AgentTask.objects.filter(pk=row.pk, version=row.version).update(
            lease_owner=owner,
            lease_expires_at=expires,
            version=row.version + 1,
            status="running" if row.status in ("pending", "blocked", "paused") else row.status,
            updated_at=now,
        )
        task.lease_owner = owner
        task.lease_expires_at = expires
        task.version = row.version + 1
        return True


def release_lease(task, owner: str) -> None:
    from orchestrator_integration.models import AgentTask

    AgentTask.objects.filter(pk=task.pk, lease_owner=owner).update(
        lease_owner="", lease_expires_at=None, updated_at=timezone.now()
    )


def consume_budget(
    task,
    *,
    tool_steps: int = 0,
    retries: int = 0,
    tokens: int = 0,
) -> Tuple[bool, str]:
    """原子扣减预算（FR07）。"""
    from orchestrator_integration.models import AgentTask

    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return False, "task_missing"
        budget = dict(row.budget or DEFAULT_BUDGET)
        next_steps = int(budget.get("used_tool_steps", 0)) + tool_steps
        next_retries = int(budget.get("used_retries", 0)) + retries
        next_tokens = int(budget.get("used_tokens", 0)) + tokens
        max_steps = int(budget.get("max_tool_steps", 28) or 28)
        max_retries = int(budget.get("max_retries", 3) or 3)
        if tool_steps and next_steps > max_steps:
            return False, f"budget_tool_steps_exceeded:{next_steps}/{max_steps}"
        if retries and next_retries > max_retries:
            return False, f"budget_retries_exceeded:{next_retries}/{max_retries}"
        budget["used_tool_steps"] = next_steps
        budget["used_retries"] = next_retries
        budget["used_tokens"] = next_tokens
        AgentTask.objects.filter(pk=row.pk, version=row.version).update(
            budget=budget,
            current_step=next_steps,
            version=row.version + 1,
            updated_at=timezone.now(),
        )
        task.budget = budget
        task.current_step = next_steps
        task.version = row.version + 1
        return True, "ok"


def request_cancel(task) -> Dict[str, Any]:
    """将任务置为 cancelling（FR07.3）。"""
    from orchestrator_integration.models import AgentTask
    from orchestrator_integration.stop_signal import set_stop_signal

    AgentTask.objects.filter(pk=task.pk).exclude(
        status__in=("completed", "failed", "cancelled")
    ).update(status="cancelling", updated_at=timezone.now())
    if task.session_id:
        try:
            set_stop_signal(task.session.session_id)
        except Exception:
            pass
    append_event(task, "task_cancelling", status="cancelling", payload={})
    return {"status": "cancelling", "task_id": task.public_task_id or str(task.id)}


def finalize_cancel(task) -> None:
    from orchestrator_integration.models import AgentTask

    AgentTask.objects.filter(pk=task.pk, status="cancelling").update(
        status="cancelled",
        completed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    append_event(task, "task_completed", status="cancelled", payload={})


def append_event(
    task,
    event_type: str,
    *,
    status: str = "",
    step_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from orchestrator_integration.models import TaskEvent

    seq = TaskEvent.objects.filter(task=task).count() + 1
    event_id = f"{task.public_task_id or task.id}-{seq}-{uuid.uuid4().hex[:6]}"
    # 脱敏：去掉疑似密钥
    safe_payload = _redact(payload or {})
    TaskEvent.objects.create(
        task=task,
        event_id=event_id,
        sequence=seq,
        step_id=step_id or "",
        event_type=event_type,
        status=status or "",
        payload=safe_payload,
    )
    return {
        "type": event_type,
        "task_id": task.public_task_id or str(task.id),
        "event_id": event_id,
        "sequence": seq,
        "step_id": step_id or "",
        "status": status or "",
        "payload": safe_payload,
    }


def record_step_result(
    task,
    *,
    tool_name: str,
    tool_input: Any,
    structured: Dict[str, Any],
    summary: str = "",
    stage_id: str = "",
) -> Any:
    from orchestrator_integration.models import AgentStep, AgentTask

    step_number = (task.current_step or 0) + 1
    status = structured.get("status") or "succeeded"
    status_map = {
        "succeeded": "succeeded",
        "failed": "failed",
        "skipped": "skipped",
        "cancelled": "cancelled",
        "running": "running",
        "pending": "pending",
        "unknown": "unknown",
        "needs_confirmation": "awaiting_approval",
    }
    step_status = status_map.get(str(status), "unknown")
    if step_status not in dict(AgentStep.STATUS_CHOICES):
        step_status = "unknown"
    step = AgentStep.objects.create(
        task=task,
        step_number=step_number,
        step_id=structured.get("operation_id") or f"step-{step_number}",
        stage_id=stage_id or task.current_stage or "",
        status=step_status,
        tool_name=tool_name or "",
        tool_input=tool_input if isinstance(tool_input, dict) else {"raw": tool_input},
        tool_output_summary=(summary or structured.get("summary") or "")[:2000],
        operation_id=structured.get("operation_id") or "",
        structured_result=structured,
        evidence=structured.get("evidence") or {},
        attempt_count=1,
        duration_ms=int(structured.get("duration_ms") or 0),
        param_fingerprint=fingerprint_payload(tool_input),
    )
    AgentTask.objects.filter(pk=task.pk).update(
        current_step=step_number,
        updated_at=timezone.now(),
    )
    task.current_step = step_number
    append_event(
        task,
        "step_result",
        status=step.status,
        step_id=step.step_id,
        payload={
            "tool_name": tool_name,
            "status": step.status,
            "stage": stage_id or task.current_stage or "",
            "resource_type": structured.get("resource_type"),
            "resource_id": structured.get("resource_id"),
            "execution_method": structured.get("execution_method"),
        },
    )
    return step


def apply_acceptance(task, acceptance: Dict[str, Any]) -> Dict[str, Any]:
    from orchestrator_integration.models import AgentTask

    ok = bool(acceptance.get("ok"))
    status = "completed" if ok else "blocked"
    AgentTask.objects.filter(pk=task.pk).update(
        acceptance=acceptance or {},
        status=status,
        completed_at=timezone.now() if ok else None,
        error_message="" if ok else "; ".join(acceptance.get("reasons") or [])[:2000],
        updated_at=timezone.now(),
    )
    evt = append_event(
        task,
        "task_completed" if ok else "task_blocked",
        status=status,
        payload={"acceptance": acceptance},
    )
    return evt


def task_snapshot(task) -> Dict[str, Any]:
    from orchestrator_integration.models import AgentStep, TaskEvent

    steps = list(
        AgentStep.objects.filter(task=task).order_by("step_number").values(
            "step_number",
            "step_id",
            "stage_id",
            "status",
            "tool_name",
            "tool_output_summary",
            "operation_id",
            "structured_result",
            "evidence",
        )[:100]
    )
    events = list(
        TaskEvent.objects.filter(task=task).order_by("sequence").values(
            "event_id", "sequence", "event_type", "status", "step_id", "payload", "created_at"
        )[:200]
    )
    return {
        "task_id": task.public_task_id or str(task.id),
        "status": task.status,
        "goal": task.goal,
        "plan_version": task.plan_version,
        "current_stage": task.current_stage,
        "stage_plan": task.stage_plan,
        "auth_scope": task.auth_scope,
        "task_scope": task.task_scope,
        "budget": task.budget,
        "acceptance": task.acceptance,
        "facts": task.facts,
        "version": task.version,
        "steps": steps,
        "events": [
            {
                **e,
                "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
            }
            for e in events
        ],
    }


def probe_browser_resources(
    *,
    session_id: str = "",
    file_path: str = "",
) -> Dict[str, Any]:
    """会话/文件资源探测（FR04.5）——轻量本地检查。"""
    import os
    from datetime import datetime

    observed_at = datetime.now().isoformat(timespec="seconds")
    result = {
        "session_ref": session_id or "",
        "file_ref": file_path or "",
        "observed_at": observed_at,
        "probe_result": {},
        "ok_to_reuse": False,
    }
    if file_path:
        exists = os.path.isfile(file_path)
        size = os.path.getsize(file_path) if exists else 0
        result["probe_result"]["file"] = {
            "accessible": exists,
            "size": size,
            "format": os.path.splitext(file_path)[1].lower(),
        }
        if not exists:
            result["probe_result"]["file"]["note"] = "file_missing_do_not_claim_upload"
    if session_id:
        alive = None
        note = "unchecked_until_playwright_ping"
        try:
            from orchestrator_integration.builtin_tools.skill_tools import (
                _get_playwright_session_manager,
            )

            mgr = _get_playwright_session_manager()
            with mgr._lock:
                entry = mgr._entries.get(session_id)
                if entry is None:
                    alive = False
                    note = "session_not_in_process_memory"
                else:
                    proc = getattr(entry, "proc", None)
                    running = bool(
                        proc
                        and getattr(proc, "_proc", None) is not None
                        and getattr(proc._proc, "poll", lambda: 0)() is None
                    )
                    alive = running
                    note = "alive_in_manager" if running else "process_exited"
        except Exception as exc:
            alive = None
            note = f"probe_error:{type(exc).__name__}"
        result["probe_result"]["session"] = {
            "claimed_id": session_id,
            "alive": alive,
            "note": note,
        }
        result["ok_to_reuse"] = alive is True
    elif file_path:
        result["ok_to_reuse"] = bool(
            (result["probe_result"].get("file") or {}).get("accessible")
        )
    return result


def format_probe_hint(probe: Dict[str, Any]) -> str:
    """把探测结果写成提示，禁止在探测失败时宣称复用成功。"""
    if not probe:
        return ""
    lines = ["# 资源探测（FR04.5）"]
    sess = (probe.get("probe_result") or {}).get("session") or {}
    file_info = (probe.get("probe_result") or {}).get("file") or {}
    if sess:
        alive = sess.get("alive")
        if alive is True:
            lines.append(
                f"- session_id={sess.get('claimed_id')} 进程内存活，可继续复用。"
            )
        elif alive is False:
            lines.append(
                f"- session_id={sess.get('claimed_id')} 不可用（{sess.get('note')}）："
                "禁止宣称复用成功，须重新开会话。"
            )
        else:
            lines.append(
                f"- session_id={sess.get('claimed_id')} 未确认存活（{sess.get('note')}）："
                "先探测/重建，勿直接当作已打开页面。"
            )
    if file_info:
        if file_info.get("accessible"):
            lines.append(
                f"- 文件可访问 size={file_info.get('size')} format={file_info.get('format')}"
            )
        else:
            lines.append(
                f"- 文件不可访问（{file_info.get('note') or 'missing'}）："
                "禁止宣称上传成功。"
            )
    lines.append(f"- observed_at={probe.get('observed_at')}")
    return "\n".join(lines)


def _extract_url(text: str) -> str:
    import re

    m = re.search(r"https?://[^\s]+", text or "", re.I)
    return m.group(0) if m else ""


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    sensitive = ("api_key", "password", "token", "secret", "authorization")
    out: Dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if any(s in str(k).lower() for s in sensitive):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


# async wrappers for agent_loop_view
create_or_resume_task_async = sync_to_async(create_or_resume_task)
append_event_async = sync_to_async(append_event)
apply_acceptance_async = sync_to_async(apply_acceptance)
consume_budget_async = sync_to_async(consume_budget)
task_snapshot_async = sync_to_async(task_snapshot)
record_step_result_async = sync_to_async(record_step_result)
