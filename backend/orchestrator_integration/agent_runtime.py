"""统一 Agent 运行时辅助层（FR04/FR07/FR10）。

HTTP 入口仍在 agent_loop_view；本模块负责任务持久化、租约、预算、事件与验收收尾。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
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
    "max_duration_ms": 0,
    "used_duration_ms": 0,
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
    turn_relation: str = "",
) -> Any:
    """创建新任务或复用同会话未完成任务（继续/补充语义）。"""
    from orchestrator_integration.models import AgentTask, AgentBlackboard
    from orchestrator_integration.turn_context import (
        TURN_CONTINUE,
        TURN_SUPPLEMENT,
        extract_constraint_patch,
    )

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
    # 「继续」关键词或显式 turn_relation=supplement/continue → 复用
    continue_like = any(
        k in (goal or "")
        for k in ("继续", "接着", "剩下的", "复用 session_id", "按上面的方案")
    )
    relation = (turn_relation or "").strip()
    should_resume = bool(existing) and (
        relation in {TURN_CONTINUE, TURN_SUPPLEMENT} or continue_like
    )
    if should_resume:
        task = existing
        if not acquire_lease(task, owner):
            return task
        # 继续/补充：保留原目标与授权，不覆盖 goal（AC17 / FR08.5）
        scope = dict(task.task_scope or {})
        if not scope.get("raw_goal"):
            scope["raw_goal"] = task.goal or ""
        msg = (goal or "")[:2000]
        if relation == TURN_SUPPLEMENT or (
            not continue_like and relation != TURN_CONTINUE
        ):
            scope["supplement_message"] = msg
            constraints = dict(scope.get("constraints") or {})
            constraints.update(extract_constraint_patch(msg))
            if constraints:
                scope["constraints"] = constraints
        else:
            scope["continue_message"] = msg
            # 「按上面的方案继续」类也可能带环境补充
            constraints = dict(scope.get("constraints") or {})
            constraints.update(extract_constraint_patch(msg))
            if constraints:
                scope["constraints"] = constraints
        task.task_scope = scope
        if auth_scope:
            merged = dict(task.auth_scope or {})
            # 只合并显式新增 allow；deny 并集；不删除原授权
            allow = set(merged.get("allow_actions") or [])
            deny = set(merged.get("deny_actions") or [])
            allow |= set(auth_scope.get("allow_actions") or [])
            deny |= set(auth_scope.get("deny_actions") or [])
            # 原 deny 优先：不得因继续话术放宽
            allow -= deny
            merged["allow_actions"] = sorted(allow)
            merged["deny_actions"] = sorted(deny)
            for k, v in auth_scope.items():
                if k not in {"allow_actions", "deny_actions"} and k not in merged:
                    merged[k] = v
            task.auth_scope = merged
        task.save(
            update_fields=[
                "task_scope",
                "auth_scope",
                "updated_at",
                "lease_owner",
                "lease_expires_at",
                "status",
            ]
        )
        return task

    budget = dict(DEFAULT_BUDGET)
    if commission is not None:
        budget["max_tool_steps"] = int(getattr(commission, "max_tool_steps", 28) or 28)
        max_tokens = int(getattr(commission, "max_tokens", 0) or 0)
        if max_tokens:
            budget["max_tokens"] = max_tokens
        max_duration = int(getattr(commission, "max_duration_ms", 0) or 0)
        if max_duration:
            budget["max_duration_ms"] = max_duration
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
    result = try_claim_execution(task, owner, ttl_minutes=ttl_minutes)
    return bool(result.get("acquired"))


def try_claim_execution(
    task, owner: str, ttl_minutes: int = 15
) -> Dict[str, Any]:
    """认领执行权（AC08）：成功或返回现有执行者状态，不抛异常。"""
    from orchestrator_integration.models import AgentTask

    now = timezone.now()
    expires = now + timedelta(minutes=ttl_minutes)
    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return {"acquired": False, "error_code": "task_missing"}
        if row.status == "cancelling":
            return {
                "acquired": False,
                "error_code": "task_cancelling",
                "status": row.status,
                "task_id": row.public_task_id or str(row.id),
            }
        if row.status in ("completed", "failed", "cancelled"):
            return {
                "acquired": False,
                "error_code": "task_terminal",
                "status": row.status,
                "task_id": row.public_task_id or str(row.id),
            }
        # 审批等待中允许 resume 接管租约（FLOW01）
        awaiting = row.status == "awaiting_approval"
        lease_ok = (
            not row.lease_owner
            or row.lease_owner == owner
            or (row.lease_expires_at and row.lease_expires_at <= now)
            or awaiting
        )
        if not lease_ok:
            return {
                "acquired": False,
                "error_code": "lease_held",
                "status": row.status,
                "lease_owner": row.lease_owner,
                "lease_expires_at": row.lease_expires_at.isoformat()
                if row.lease_expires_at
                else None,
                "task_id": row.public_task_id or str(row.id),
                "version": row.version,
            }
        new_status = (
            "running"
            if row.status in ("pending", "blocked", "paused", "awaiting_approval")
            else row.status
        )
        updated = AgentTask.objects.filter(pk=row.pk, version=row.version).update(
            lease_owner=owner,
            lease_expires_at=expires,
            version=row.version + 1,
            status=new_status,
            updated_at=now,
        )
        if not updated:
            return {"acquired": False, "error_code": "version_conflict"}
        task.lease_owner = owner
        task.lease_expires_at = expires
        task.version = row.version + 1
        task.status = new_status
        return {
            "acquired": True,
            "status": new_status,
            "task_id": row.public_task_id or str(row.id),
            "version": task.version,
            "lease_owner": owner,
        }


def heartbeat_lease(task, owner: str, ttl_minutes: int = 15) -> bool:
    """续租心跳；非持有者失败。"""
    from orchestrator_integration.models import AgentTask

    now = timezone.now()
    expires = now + timedelta(minutes=ttl_minutes)
    n = AgentTask.objects.filter(pk=task.pk, lease_owner=owner).update(
        lease_expires_at=expires, updated_at=now
    )
    if n:
        task.lease_expires_at = expires
    return bool(n)


def release_lease(task, owner: str = "") -> None:
    from orchestrator_integration.models import AgentTask

    qs = AgentTask.objects.filter(pk=task.pk)
    if owner:
        qs = qs.filter(lease_owner=owner)
    qs.update(lease_owner="", lease_expires_at=None, updated_at=timezone.now())
    task.lease_owner = ""
    task.lease_expires_at = None


def park_for_approval(task, *, interrupt_id: str = "") -> None:
    """写入确认发出时释放租约并进入 awaiting_approval，供 resume 接管（FLOW01）。"""
    from orchestrator_integration.models import AgentTask

    AgentTask.objects.filter(pk=task.pk).exclude(
        status__in=("completed", "failed", "cancelled", "cancelling")
    ).update(
        status="awaiting_approval",
        lease_owner="",
        lease_expires_at=None,
        updated_at=timezone.now(),
    )
    task.status = "awaiting_approval"
    task.lease_owner = ""
    task.lease_expires_at = None
    append_event(
        task,
        "approval_required",
        status="awaiting_approval",
        payload={"interrupt_id": interrupt_id or ""},
    )


def assert_task_access(
    task, *, user_id: int, project_id: Optional[int] = None, session_id: str = ""
) -> Dict[str, Any]:
    """归属校验（FR05.1）：用户/项目成员/任务归属统一入口（R25）。"""
    if task.user_id and int(task.user_id) != int(user_id):
        return {"ok": False, "error_code": "permission_denied"}
    if (
        project_id is not None
        and task.project_id is not None
        and int(task.project_id) != int(project_id)
    ):
        return {"ok": False, "error_code": "project_mismatch"}
    # 任务绑定项目时，当前用户必须是项目成员（即便任务归属本人）
    effective_project_id = task.project_id
    if effective_project_id is not None:
        try:
            from projects.models import Project, ProjectMember

            project = Project.objects.filter(pk=effective_project_id).first()
            if project is not None:
                is_creator = int(getattr(project, "creator_id", 0) or 0) == int(user_id)
                is_member = ProjectMember.objects.filter(
                    project_id=effective_project_id, user_id=user_id
                ).exists()
                if not is_creator and not is_member:
                    return {"ok": False, "error_code": "project_membership_denied"}
        except Exception:
            # 项目模块不可用时不放宽：有 project_id 却无法核验则拒绝
            return {"ok": False, "error_code": "project_membership_denied"}
    if session_id:
        try:
            sid = task.session.session_id
        except Exception:
            sid = ""
        if sid and sid != session_id:
            return {"ok": False, "error_code": "session_mismatch"}
    return {"ok": True}


def resume_preflight(task) -> Dict[str, Any]:
    """恢复前核对：不确定步骤必须先 reconcile（FR04.4 / AC09）。"""
    from orchestrator_integration.models import AgentStep
    from orchestrator_integration.reconcile import collect_uncertain_steps

    steps = list(
        AgentStep.objects.filter(task=task)
        .order_by("step_number")
        .values(
            "step_id",
            "tool_name",
            "status",
            "tool_output_summary",
            "structured_result",
            "operation_id",
        )[:100]
    )
    uncertain = collect_uncertain_steps(steps)
    return {
        "must_reconcile": bool(uncertain),
        "uncertain_steps": uncertain,
        "hint": (
            "存在未核对的副作用步骤：先查询原动作状态，禁止盲目重放。"
            if uncertain
            else ""
        ),
    }


def consume_budget(
    task,
    *,
    tool_steps: int = 0,
    retries: int = 0,
    tokens: int = 0,
    duration_ms: int = 0,
) -> Tuple[bool, str]:
    """原子扣减预算（FR07）。"""
    from orchestrator_integration.models import AgentTask

    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return False, "task_missing"
        budget = dict(DEFAULT_BUDGET)
        budget.update(row.budget or {})
        next_steps = int(budget.get("used_tool_steps", 0)) + tool_steps
        next_retries = int(budget.get("used_retries", 0)) + retries
        next_tokens = int(budget.get("used_tokens", 0)) + tokens
        next_duration = int(budget.get("used_duration_ms", 0)) + max(0, duration_ms)
        max_steps = int(budget.get("max_tool_steps", 28) or 28)
        max_retries = int(budget.get("max_retries", 3) or 3)
        max_duration = int(budget.get("max_duration_ms", 0) or 0)
        max_tokens = int(budget.get("max_tokens", 0) or 0)
        # R21：实际已消耗量必须入账；超额时标记但仍写入 used_*，不能抹去花费
        exceeded = ""
        if tool_steps and next_steps > max_steps:
            exceeded = f"budget_tool_steps_exceeded:{next_steps}/{max_steps}"
        elif retries and next_retries > max_retries:
            exceeded = f"budget_retries_exceeded:{next_retries}/{max_retries}"
        elif max_duration and duration_ms and next_duration > max_duration:
            exceeded = f"budget_duration_exceeded:{next_duration}/{max_duration}"
        elif tokens and max_tokens and next_tokens > max_tokens:
            exceeded = f"budget_tokens_exceeded:{next_tokens}/{max_tokens}"
        budget["used_tool_steps"] = next_steps
        budget["used_retries"] = next_retries
        budget["used_tokens"] = next_tokens
        budget["used_duration_ms"] = next_duration
        if exceeded:
            budget["over_budget"] = True
            budget["over_budget_reason"] = exceeded
        AgentTask.objects.filter(pk=row.pk, version=row.version).update(
            budget=budget,
            current_step=next_steps if tool_steps else row.current_step,
            version=row.version + 1,
            updated_at=timezone.now(),
        )
        task.budget = budget
        if tool_steps:
            task.current_step = next_steps
        task.version = row.version + 1
        if exceeded:
            return False, exceeded
        return True, "ok"


def request_cancel(task) -> Dict[str, Any]:
    """将任务置为 cancelling（FR07.3）。在途步骤保留查询能力。"""
    from orchestrator_integration.models import AgentStep, AgentTask
    from orchestrator_integration.stop_signal import set_stop_signal

    AgentTask.objects.filter(pk=task.pk).exclude(
        status__in=("completed", "failed", "cancelled")
    ).update(status="cancelling", updated_at=timezone.now())
    in_flight = list(
        AgentStep.objects.filter(task=task, status__in=("pending", "running")).values(
            "step_id", "tool_name", "status", "operation_id"
        )[:50]
    )
    try:
        set_stop_signal(task.session.session_id)
    except Exception:
        pass
    evt = append_event(
        task,
        "task_cancelling",
        status="cancelling",
        payload={"in_flight": in_flight},
    )
    return {
        "status": "cancelling",
        "task_id": task.public_task_id or str(task.id),
        "in_flight": in_flight,
        "event_id": evt.get("event_id"),
        "note": "已阻止新步骤；在途外部动作需继续查询，不能视为已撤销",
    }


def finalize_cancel(task, *, force: bool = False) -> Dict[str, Any]:
    """确认在途结束后标记 cancelled（FR07.4 / FLOW04）。

    unknown 且带外部 execution/operation 的步骤视为未核对副作用，禁止宣称已取消。
    """
    from orchestrator_integration.models import AgentStep, AgentTask

    in_flight = list(
        AgentStep.objects.filter(task=task, status__in=("pending", "running")).values(
            "step_id", "status", "operation_id"
        )[:50]
    )
    uncertain = []
    for s in AgentStep.objects.filter(task=task, status="unknown").values(
        "step_id", "operation_id", "structured_result", "tool_output_summary"
    )[:50]:
        structured = s.get("structured_result") or {}
        data = structured.get("data") if isinstance(structured, dict) else {}
        has_ext = bool(
            s.get("operation_id")
            or (isinstance(data, dict) and data.get("execution_id"))
            or "execution_id" in str(s.get("tool_output_summary") or "")
        )
        if has_ext:
            uncertain.append(s)
    if (in_flight or uncertain) and not force:
        return {
            "status": "cancelling",
            "task_id": task.public_task_id or str(task.id),
            "in_flight_count": len(in_flight),
            "uncertain_count": len(uncertain),
            "uncertain_steps": uncertain,
            "note": "仍有在途或未核对外部动作，保持 cancelling，不能视为业务影响已停止",
        }
    AgentTask.objects.filter(pk=task.pk, status="cancelling").update(
        status="cancelled",
        completed_at=timezone.now(),
        updated_at=timezone.now(),
        lease_owner="",
        lease_expires_at=None,
    )
    append_event(task, "task_completed", status="cancelled", payload={})
    return {
        "status": "cancelled",
        "task_id": task.public_task_id or str(task.id),
        "in_flight_count": 0,
        "uncertain_count": 0,
    }


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
    evidence = dict(structured.get("evidence") or {})
    # FR06.3：unknown 步骤附带核对计划，恢复时禁止盲目重放
    if step_status == "unknown":
        try:
            from orchestrator_integration.reconcile import build_reconcile_plan

            plan = build_reconcile_plan(
                skill_name=tool_name or "",
                text=summary or structured.get("summary") or "",
                status="unknown",
                structured=structured,
            )
            evidence["reconcile"] = plan
            structured = dict(structured)
            structured["reconcile"] = plan
        except Exception:
            pass
    if structured.get("idempotency_key"):
        evidence["idempotency_key"] = structured.get("idempotency_key")

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
        evidence=evidence,
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
    """终态条件更新：不可逆；完成事件唯一（R20）。"""
    from orchestrator_integration.models import AgentTask, TaskEvent

    ok = bool(acceptance.get("ok"))
    status = "completed" if ok else "blocked"
    terminal = ("completed", "failed", "cancelled")
    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return {"type": "error", "error_code": "task_missing"}
        if row.status in terminal:
            # 禁止迟到验收覆盖取消/失败/已完成
            return {
                "type": "task_status",
                "status": row.status,
                "task_id": row.public_task_id or str(row.id),
                "ignored": True,
                "error_code": "already_terminal",
            }
        if ok:
            already = TaskEvent.objects.filter(
                task=row, event_type="task_completed"
            ).exists()
            if already:
                return {
                    "type": "task_completed",
                    "status": "completed",
                    "task_id": row.public_task_id or str(row.id),
                    "ignored": True,
                    "error_code": "duplicate_completion",
                }
        AgentTask.objects.filter(pk=row.pk).exclude(status__in=terminal).update(
            acceptance=acceptance or {},
            status=status,
            completed_at=timezone.now() if ok else None,
            error_message=""
            if ok
            else "; ".join(acceptance.get("reasons") or [])[:2000],
            updated_at=timezone.now(),
            lease_owner="",
            lease_expires_at=None,
        )
        task.status = status
        task.acceptance = acceptance or {}
    evt = append_event(
        task,
        "task_completed" if ok else "task_blocked",
        status=status,
        payload={"acceptance": acceptance},
    )
    # 统一时间字段
    if "time" not in evt:
        evt["time"] = timezone.now().isoformat()
    return evt


def task_snapshot(
    task, *, after_event_id: str = "", limit: int = 200
) -> Dict[str, Any]:
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
    event_qs = TaskEvent.objects.filter(task=task).order_by("sequence")
    after_seq = 0
    if after_event_id:
        after = (
            TaskEvent.objects.filter(event_id=after_event_id, task=task)
            .values_list("sequence", flat=True)
            .first()
        )
        if after is not None:
            after_seq = int(after)
            event_qs = event_qs.filter(sequence__gt=after_seq)
    total_after = event_qs.count()
    events = list(
        event_qs.values(
            "event_id",
            "sequence",
            "event_type",
            "status",
            "step_id",
            "payload",
            "created_at",
        )[: max(1, int(limit))]
    )
    has_more = total_after > len(events)
    next_cursor = events[-1]["event_id"] if events else after_event_id or ""
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
        "has_more": has_more,
        "next_cursor": next_cursor,
        "events": [
            {
                **e,
                "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
                "time": e["created_at"].isoformat() if e.get("created_at") else None,
            }
            for e in events
        ],
    }


def _ping_domain(url_or_host: str, timeout: float = 2.5) -> Dict[str, Any]:
    """对目标域名做轻量可达探测（不跟随复杂登录态）。"""
    import socket
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    raw = (url_or_host or "").strip()
    if not raw:
        return {"ok": False, "error": "empty_host"}
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host:
        return {"ok": False, "error": "bad_host"}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    dns_ok = False
    tcp_ok = False
    http_status = None
    err = ""
    try:
        socket.getaddrinfo(host, port)
        dns_ok = True
    except OSError as exc:
        err = f"dns:{exc}"
    if dns_ok:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                tcp_ok = True
        except OSError as exc:
            err = f"tcp:{exc}"
    if tcp_ok:
        try:
            req = Request(
                f"{parsed.scheme}://{host}/",
                method="HEAD",
                headers={"User-Agent": "loca-stude-probe/1.0"},
            )
            with urlopen(req, timeout=timeout) as resp:  # nosec B310 - user target
                http_status = getattr(resp, "status", None) or 200
        except Exception as exc:
            # HEAD 失败不否定 TCP 可达
            err = f"http:{type(exc).__name__}"
            http_status = None
    return {
        "ok": bool(tcp_ok),
        "host": host,
        "dns_ok": dns_ok,
        "tcp_ok": tcp_ok,
        "http_status": http_status,
        "error": err,
    }


def probe_browser_resources(
    *,
    session_id: str = "",
    file_path: str = "",
    target_url: str = "",
) -> Dict[str, Any]:
    """会话/文件/域名资源探测（FR04.5）——本地 + 轻量网络检查。"""
    import os
    from datetime import datetime

    observed_at = datetime.now().isoformat(timespec="seconds")
    result = {
        "session_ref": session_id or "",
        "file_ref": file_path or "",
        "target_url": target_url or "",
        "observed_at": observed_at,
        "probe_result": {},
        "ok_to_reuse": False,
    }
    if target_url:
        result["probe_result"]["domain"] = _ping_domain(target_url)
    if file_path:
        exists = os.path.isfile(file_path)
        size = os.path.getsize(file_path) if exists else 0
        content_version = ""
        if exists:
            try:
                h = hashlib.sha256()
                with open(file_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                content_version = h.hexdigest()[:16]
            except OSError:
                content_version = ""
        result["probe_result"]["file"] = {
            "accessible": exists,
            "size": size,
            "format": os.path.splitext(file_path)[1].lower(),
            "content_version": content_version,
        }
        if not exists:
            result["probe_result"]["file"]["note"] = "file_missing_do_not_claim_upload"
    if session_id:
        alive = None
        note = "unchecked_until_playwright_ping"
        login_state = "unknown"
        page_url = ""
        cookie_count = 0
        login_hint = ""
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
                login_state = "unavailable"
            else:
                meta = mgr.probe_session(session_id) or {}
                alive = meta.get("alive")
                if alive is None:
                    # 回退进程存活判断
                    proc = getattr(entry, "proc", None)
                    alive = bool(
                        proc
                        and getattr(proc, "_proc", None) is not None
                        and getattr(proc._proc, "poll", lambda: 0)() is None
                    )
                note = str(meta.get("login_hint") or (
                    "alive_in_manager" if alive else "process_exited"
                ))
                page_url = str(meta.get("last_url") or "")
                cookie_count = int(meta.get("cookie_count") or 0)
                login_hint = str(meta.get("login_hint") or "")
                if meta.get("logged_in") is True:
                    login_state = "logged_in"
                elif meta.get("logged_in") is False:
                    login_state = "logged_out"
                elif alive:
                    login_state = "process_alive_login_unchecked"
                else:
                    login_state = "offline"
        except Exception as exc:
            alive = None
            note = f"probe_error:{type(exc).__name__}"
            login_state = "probe_error"
        result["probe_result"]["session"] = {
            "claimed_id": session_id,
            "alive": alive,
            "note": note,
            "login_state": login_state,
            "login_hint": login_hint,
            "page_url": page_url,
            "cookie_count": cookie_count,
        }
        domain = (result["probe_result"].get("domain") or {})
        # 可复用：进程活着 + 域名可达；登录态仅作提示，不单独否决加购页匿名态
        result["ok_to_reuse"] = bool(alive is True and domain.get("ok", True))
    elif file_path:
        file_ok = bool((result["probe_result"].get("file") or {}).get("accessible"))
        domain = result["probe_result"].get("domain") or {}
        result["ok_to_reuse"] = file_ok and domain.get("ok", True)
    elif target_url:
        result["ok_to_reuse"] = bool((result["probe_result"].get("domain") or {}).get("ok"))
    return result


def format_probe_hint(probe: Dict[str, Any]) -> str:
    """把探测结果写成提示，禁止在探测失败时宣称复用成功。"""
    if not probe:
        return ""
    lines = ["# 资源探测（FR04.5）"]
    sess = (probe.get("probe_result") or {}).get("session") or {}
    file_info = (probe.get("probe_result") or {}).get("file") or {}
    domain = (probe.get("probe_result") or {}).get("domain") or {}
    if domain:
        if domain.get("ok"):
            lines.append(
                f"- 域名 {domain.get('host')} 可达"
                + (
                    f"（HTTP {domain.get('http_status')}）"
                    if domain.get("http_status")
                    else "（TCP ok）"
                )
            )
        else:
            lines.append(
                f"- 域名 {domain.get('host') or '(未知)'} 不可达"
                f"（{domain.get('error') or 'ping_failed'}）："
                "禁止宣称页面已打开。"
            )
    if sess:
        alive = sess.get("alive")
        login = sess.get("login_state") or "unknown"
        if alive is True:
            lines.append(
                f"- session_id={sess.get('claimed_id')} 进程内存活；"
                f"login_state={login}"
                + (f"；page={sess.get('page_url')}" if sess.get("page_url") else "")
                + "。登录态未核验前勿假设已登录。"
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
                f"- 文件可访问 size={file_info.get('size')} "
                f"format={file_info.get('format')} "
                f"content_version={file_info.get('content_version') or '-'}"
            )
        else:
            lines.append(
                f"- 文件不可访问（{file_info.get('note') or 'missing'}）："
                "禁止宣称上传成功。"
            )
    if sess:
        cookie_count = sess.get("cookie_count")
        if cookie_count is not None:
            lines.append(
                f"- cookie_count={cookie_count} 仅为探测快照，不等于当前登录有效；"
                "禁止据此写「cookie 仍在」或「会话仍存活」。"
            )
    lines.append(f"- observed_at={probe.get('observed_at')}")
    lines.append(f"- ok_to_reuse={probe.get('ok_to_reuse')}")
    lines.append(
        "- 历史文件/会话存在 ≠ 本轮可读或登录有效；ok_to_reuse=False 时禁止宣称"
        "「完整执行输入已具备」。"
    )
    lines.append(
        "- 上一轮 HTTP 502/5xx 只能标「历史异常，尚未复核」；禁止当作本轮仍失败，"
        "也禁止据此要求用户先恢复环境（除非本轮探测再次失败）。"
    )
    return "\n".join(lines)


def stage_acceptance_heuristic(
    stage_id: str, tool_results: Optional[List[str]] = None
) -> Dict[str, Any]:
    """阶段级启发式验收（AC16）：用于自动推进，非整任务终态。

    失败/拒绝文本不得仅凭动作名被当作已完成（FLOW07）。
    """
    joined = "\n".join(str(x) for x in (tool_results or []) if x)
    sid = stage_id or ""
    low = joined.lower()
    failure_markers = (
        '"status": "failed"',
        '"status":"failed"',
        "permission denied",
        "权限不足",
        "无权限",
        "余额不足",
        "业务拒绝",
        "错误:",
        "错误：",
        "timeout",
        "超时",
    )
    if any(m in low or m in joined for m in failure_markers):
        # 允许 execute 阶段报告「测试失败终态」为完成；其它阶段失败不得推进
        if sid not in {"execute", "execute_testcase"} or "终态" not in joined:
            if not (
                sid in {"execute", "execute_testcase"}
                and any(k in joined for k in ("终态", "execution_id", "get_execution_records"))
                and '"status": "failed"' not in joined  # tool failed vs test failed
            ):
                if any(
                    m in low
                    for m in (
                        "permission denied",
                        "权限不足",
                        "无权限",
                        "余额不足",
                        "业务拒绝",
                    )
                ) or '"status": "failed"' in joined or '"status":"failed"' in joined:
                    return {
                        "ok": False,
                        "reason": "failure_evidence_present",
                        "stage_id": sid,
                    }
                if any(m in low for m in ("timeout", "超时")) and "终态" not in joined:
                    return {
                        "ok": False,
                        "reason": "timeout_without_terminal",
                        "stage_id": sid,
                    }

    ok = False
    reason = ""
    if sid in {"kb_research", "single"} and (
        "预检索" in joined
        or "knowledge_search" in joined
        or ("来源" in joined and "失败" not in joined)
        or '"status": "succeeded"' in joined
    ):
        ok, reason = True, "kb_or_generic_evidence"
    elif sid == "generate_draft" and any(
        k in joined for k in ("草稿", "testcase", "steps", "用例")
    ):
        ok, reason = True, "draft_markers"
    elif sid == "persist":
        # 必须有真实产物 ID，禁止仅凭 add_testcase 命令名
        if re.search(
            r"(testcase_id|case_id)\s*[:=]\s*[A-Za-z0-9_-]+|创建成功.*id|\"id\"\s*:\s*\d+",
            joined,
            re.I,
        ):
            ok, reason = True, "persist_id"
        else:
            ok, reason = False, "persist_missing_id"
    elif sid in {"execute", "execute_testcase"} and any(
        k in joined for k in ("execution_id", "终态", "get_execution_records")
    ):
        ok, reason = True, "execution_evidence"
    elif sid == "browser_interact" and re.search(
        r"(cart_id|cart_item_id)\s*[:=]\s*(?!unknown\b|null\b|none\b)\S+",
        joined,
        re.I,
    ):
        ok, reason = True, "browser_typed_id"
    elif sid == "summarize":
        ok, reason = True, "summarize_ready"
    return {"ok": ok, "reason": reason, "stage_id": sid}


def advance_task_stage(
    task,
    *,
    tool_results: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """阶段验收通过则推进并递增 plan_version（FR08.3 / AC16）。"""
    from orchestrator_integration.models import AgentTask
    from orchestrator_integration.stage_plan import StageSpec, advance_stage

    stages_raw = list(task.stage_plan or [])
    if len(stages_raw) <= 1:
        return {
            "advanced": False,
            "current_stage": task.current_stage or "",
            "reason": "single_or_empty_plan",
        }
    stages = [
        StageSpec(
            stage_id=str(s.get("stage_id") or ""),
            goal=str(s.get("goal") or ""),
            tool_capabilities=list(s.get("tool_capabilities") or []),
            depends_on=list(s.get("depends_on") or []),
            acceptance=str(s.get("acceptance") or ""),
            auth_actions=list(s.get("auth_actions") or []),
        )
        for s in stages_raw
        if isinstance(s, dict)
    ]
    cur = task.current_stage or (stages[0].stage_id if stages else "")
    verdict = stage_acceptance_heuristic(cur, tool_results)
    if not force and not verdict.get("ok"):
        return {
            "advanced": False,
            "current_stage": cur,
            "reason": "stage_not_accepted",
            "verdict": verdict,
        }
    nxt = advance_stage(stages, cur, accepted=True)
    if nxt == cur:
        return {
            "advanced": False,
            "current_stage": cur,
            "reason": "already_last_stage",
            "verdict": verdict,
        }
    new_version = int(task.plan_version or 1) + 1
    AgentTask.objects.filter(pk=task.pk).update(
        current_stage=nxt,
        plan_version=new_version,
        updated_at=timezone.now(),
    )
    task.current_stage = nxt
    task.plan_version = new_version
    evt = append_event(
        task,
        "stage_changed",
        status="running",
        payload={
            "from_stage": cur,
            "to_stage": nxt,
            "plan_version": new_version,
            "verdict": verdict,
        },
    )
    return {
        "advanced": True,
        "from_stage": cur,
        "current_stage": nxt,
        "plan_version": new_version,
        "event": evt,
        "verdict": verdict,
    }


def _extract_url(text: str) -> str:
    import re

    m = re.search(r"https?://[^\s]+", text or "", re.I)
    return m.group(0) if m else ""


def _redact(payload: Any) -> Any:
    """递归脱敏 dict/list/tuple（R27）。"""
    sensitive = ("api_key", "password", "token", "secret", "authorization")
    if isinstance(payload, dict):
        out: Dict[str, Any] = {}
        for k, v in payload.items():
            if any(s in str(k).lower() for s in sensitive):
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(payload, list):
        return [_redact(x) for x in payload]
    if isinstance(payload, tuple):
        return tuple(_redact(x) for x in payload)
    return payload


class AgentRuntime:
    """统一运行时门面（FR04.1）。

    HTTP/SSE 仍由 agent_loop_view 负责；任务状态、租约、预算、探测、
    阶段推进、验收与指标经由此类，保证首次执行与 resume 共用规则。
    """

    def __init__(self, *, lease_owner: str = ""):
        self.lease_owner = lease_owner or f"worker-{uuid.uuid4().hex[:8]}"
        self.task = None

    def start(
        self,
        *,
        session,
        user,
        project_id: Optional[int],
        goal: str,
        commission,
        auth_scope: Optional[Dict[str, Any]] = None,
        stage_plan: Optional[List[Dict[str, Any]]] = None,
        turn_relation: str = "",
    ):
        self.task = create_or_resume_task(
            session=session,
            user=user,
            project_id=project_id,
            goal=goal,
            commission=commission,
            auth_scope=auth_scope,
            stage_plan=stage_plan,
            lease_owner=self.lease_owner,
            turn_relation=turn_relation,
        )
        return self.task

    def claim(self, task) -> Dict[str, Any]:
        self.task = task
        return try_claim_execution(task, self.lease_owner)

    def heartbeat(self) -> bool:
        if self.task is None:
            return False
        return heartbeat_lease(self.task, self.lease_owner)

    def access_ok(
        self, *, user_id: int, project_id: Optional[int] = None, session_id: str = ""
    ) -> Dict[str, Any]:
        if self.task is None:
            return {"ok": False, "error_code": "task_missing"}
        return assert_task_access(
            self.task, user_id=user_id, project_id=project_id, session_id=session_id
        )

    def preflight(self) -> Dict[str, Any]:
        if self.task is None:
            return {"must_reconcile": False}
        return resume_preflight(self.task)

    def probe(
        self,
        *,
        session_id: str = "",
        file_path: str = "",
        target_url: str = "",
    ) -> Dict[str, Any]:
        url = target_url
        if not url and self.task is not None:
            url = str((self.task.task_scope or {}).get("target_url") or "")
        return probe_browser_resources(
            session_id=session_id, file_path=file_path, target_url=url
        )

    def spend(
        self, *, tool_steps: int = 0, retries: int = 0, tokens: int = 0, duration_ms: int = 0
    ) -> Tuple[bool, str]:
        if self.task is None:
            return False, "task_missing"
        return consume_budget(
            self.task,
            tool_steps=tool_steps,
            retries=retries,
            tokens=tokens,
            duration_ms=duration_ms,
        )

    def record_step(self, **kwargs):
        if self.task is None:
            return None
        return record_step_result(self.task, **kwargs)

    def maybe_advance(self, tool_results: Optional[List[str]] = None) -> Dict[str, Any]:
        if self.task is None:
            return {"advanced": False}
        return advance_task_stage(self.task, tool_results=tool_results)

    def accept(self, acceptance: Dict[str, Any]) -> Dict[str, Any]:
        if self.task is None:
            return {}
        # 多阶段：未到末阶段不得标 completed；推进只由 maybe_advance 负责（FLOW02）
        # R11：刚进入末阶段但末阶段自身验收未通过时，也不得整任务 completed
        # 单阶段 browser_interact 同样要求阶段证据，禁止无动作 completed
        stages = self.task.stage_plan or []
        cur = self.task.current_stage or ""
        if acceptance.get("ok"):
            ids = [str(s.get("stage_id") or "") for s in stages if isinstance(s, dict)]
            if len(stages) > 1 and cur and ids and cur != ids[-1]:
                return {
                    "type": "stage_changed",
                    "status": "running",
                    "payload": {
                        "current_stage": cur,
                        "advanced": False,
                        "reason": "not_last_stage_defer_completion",
                        "plan_version": self.task.plan_version,
                    },
                    "deferred_completion": True,
                }
            need_stage_proof = bool(
                cur == "browser_interact"
                or (len(stages) > 1 and cur and ids and cur == ids[-1])
            )
            if need_stage_proof:
                tool_results = list((acceptance.get("tool_results") or []))
                last_verdict = stage_acceptance_heuristic(cur, tool_results)
                if not tool_results:
                    try:
                        from orchestrator_integration.models import AgentStep

                        recent = list(
                            AgentStep.objects.filter(task=self.task, stage_id=cur)
                            .order_by("-step_number")
                            .values_list("tool_output_summary", flat=True)[:20]
                        )
                        last_verdict = stage_acceptance_heuristic(cur, recent)
                    except Exception:
                        last_verdict = {"ok": False, "reason": "last_stage_unproven"}
                if not last_verdict.get("ok"):
                    return {
                        "type": "stage_changed",
                        "status": "running",
                        "payload": {
                            "current_stage": cur,
                            "advanced": False,
                            "reason": "last_stage_pending_evidence",
                            "verdict": last_verdict,
                            "plan_version": self.task.plan_version,
                        },
                        "deferred_completion": True,
                    }
        return apply_acceptance(self.task, acceptance)

    def cancel(self) -> Dict[str, Any]:
        if self.task is None:
            return {"status": "missing"}
        return request_cancel(self.task)

    def snapshot(self) -> Dict[str, Any]:
        if self.task is None:
            return {}
        return task_snapshot(self.task)

    def release(self) -> None:
        if self.task is not None:
            release_lease(self.task, self.lease_owner)


def quality_metrics(*, user_id: Optional[int] = None, limit: int = 200) -> Dict[str, Any]:
    """质量指标（FR10.4）：任务验收 + 工具遥测聚合。"""
    from orchestrator_integration.models import AgentTask, TaskEvent
    from orchestrator_integration.tool_telemetry import aggregate_telemetry

    qs = AgentTask.objects.all()
    if user_id:
        qs = qs.filter(user_id=user_id)
    recent = list(qs.order_by("-updated_at")[:limit])
    total = len(recent) or 1
    by_status = {}
    false_complete = 0
    blocked = 0
    completed = 0
    for t in recent:
        st = t.status or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        if st == "completed":
            completed += 1
            acc = t.acceptance or {}
            if acc and not acc.get("ok", True):
                false_complete += 1
        if st == "blocked":
            blocked += 1
    stage_events = TaskEvent.objects.filter(
        event_type="stage_changed", task_id__in=[t.id for t in recent]
    ).count()
    # R26：遥测按用户过滤，与任务列表范围一致
    telem = aggregate_telemetry(limit=2000, user_id=user_id)
    return {
        "tasks_sampled": len(recent),
        "status_counts": by_status,
        "completion_rate": round(completed / total, 4),
        "block_rate": round(blocked / total, 4),
        "false_complete_count": false_complete,
        "stage_advance_events": stage_events,
        "telemetry": {
            "governance_kpis": telem.get("governance_kpis"),
            "error_kinds": telem.get("error_kinds"),
            "playbook_hits": telem.get("playbook_hits"),
            "total_tool_events": telem.get("total"),
        },
    }


# async wrappers for agent_loop_view
create_or_resume_task_async = sync_to_async(create_or_resume_task)
append_event_async = sync_to_async(append_event)
apply_acceptance_async = sync_to_async(apply_acceptance)
consume_budget_async = sync_to_async(consume_budget)
task_snapshot_async = sync_to_async(task_snapshot)
record_step_result_async = sync_to_async(record_step_result)
try_claim_execution_async = sync_to_async(try_claim_execution)
heartbeat_lease_async = sync_to_async(heartbeat_lease)
resume_preflight_async = sync_to_async(resume_preflight)
assert_task_access_async = sync_to_async(assert_task_access)
finalize_cancel_async = sync_to_async(finalize_cancel)
advance_task_stage_async = sync_to_async(advance_task_stage)
quality_metrics_async = sync_to_async(quality_metrics)
