"""第 5 阶段：生产可观测性与稳定性。

目标：线上出问题时可定位、恢复并衡量，而不是靠聊天截图猜测。
- 任务/步骤/工具调用统一 ID、起止时间、状态、重试、错误类型
- 统计悬挂、续跑成功率、重复操作率、完成率、平均耗时
- 心跳、超时回收、异常告警
- 状态迁移 / 授权变更 / 审批 / 最终交付审计
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence

from django.utils import timezone

logger = logging.getLogger(__name__)

# 审计事件类型
AUDIT_STATUS_TRANSITION = "status_transition"
AUDIT_AUTH_CHANGED = "auth_changed"
AUDIT_APPROVAL_DECISION = "approval_decision"
AUDIT_FINAL_DELIVERY = "final_delivery"
AUDIT_HEARTBEAT = "runtime_heartbeat"
AUDIT_ANOMALY_ALERT = "anomaly_alert"
AUDIT_STALE_RECLAIM = "stale_reclaim"


def lifecycle_envelope(
    *,
    entity: str,
    entity_id: str,
    status: str,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    attempt_count: int = 0,
    error_type: str = "",
    error_message: str = "",
    parent_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一生命周期字段（任务 / 步骤 / 工具调用）。"""
    now = timezone.now().isoformat()
    out = {
        "schema_version": 1,
        "entity": entity,  # task | step | tool_call
        "id": str(entity_id or ""),
        "parent_id": str(parent_id or ""),
        "status": str(status or ""),
        "started_at": started_at or now,
        "ended_at": ended_at or "",
        "attempt_count": int(attempt_count or 0),
        "error_type": str(error_type or ""),
        "error_message": str(error_message or "")[:2000],
    }
    if extra:
        out["extra"] = dict(extra)
    return out


def task_lifecycle(task) -> Dict[str, Any]:
    st = str(getattr(task, "status", "") or "")
    started = getattr(task, "created_at", None)
    ended = getattr(task, "completed_at", None)
    err = str(getattr(task, "error_message", "") or "")
    error_type = ""
    acc = getattr(task, "acceptance", None) or {}
    if isinstance(acc, dict):
        blockers = acc.get("blockers") or []
        if blockers and isinstance(blockers[0], dict):
            error_type = str(blockers[0].get("blocker_code") or "")
        if not error_type and not acc.get("ok", True):
            error_type = "acceptance_failed"
    if st == "blocked" and not error_type:
        error_type = "blocked"
    return lifecycle_envelope(
        entity="task",
        entity_id=getattr(task, "public_task_id", None) or str(getattr(task, "pk", "")),
        status=st,
        started_at=started.isoformat() if started else "",
        ended_at=ended.isoformat() if ended else "",
        attempt_count=int((getattr(task, "budget", None) or {}).get("resume_count") or 0),
        error_type=error_type,
        error_message=err,
        extra={
            "lease_owner": getattr(task, "lease_owner", "") or "",
            "lease_expires_at": (
                task.lease_expires_at.isoformat()
                if getattr(task, "lease_expires_at", None)
                else ""
            ),
            "current_stage": getattr(task, "current_stage", "") or "",
        },
    )


def step_lifecycle(step, *, task_id: str = "") -> Dict[str, Any]:
    st = str(getattr(step, "status", "") or "")
    created = getattr(step, "created_at", None)
    duration = int(getattr(step, "duration_ms", None) or 0)
    ended = ""
    if created and duration > 0:
        ended = (created + timedelta(milliseconds=duration)).isoformat()
    elif st in {"succeeded", "failed", "skipped", "cancelled", "blocked"}:
        ended = created.isoformat() if created else ""
    structured = getattr(step, "structured_result", None) or {}
    err_type = ""
    err_msg = ""
    if isinstance(structured, dict):
        err_type = str(
            structured.get("error_type")
            or structured.get("reason")
            or structured.get("error_code")
            or ""
        )
        err_msg = str(structured.get("summary") or structured.get("error") or "")
    if st in {"failed", "unknown", "blocked"} and not err_type:
        err_type = st
    return lifecycle_envelope(
        entity="step",
        entity_id=str(
            getattr(step, "step_id", None)
            or getattr(step, "operation_id", None)
            or getattr(step, "pk", "")
        ),
        status=st,
        started_at=created.isoformat() if created else "",
        ended_at=ended,
        attempt_count=int(getattr(step, "attempt_count", None) or 1),
        error_type=err_type,
        error_message=err_msg,
        parent_id=task_id,
        extra={
            "tool_name": getattr(step, "tool_name", "") or "",
            "stage_id": getattr(step, "stage_id", "") or "",
            "duration_ms": duration,
            "operation_id": getattr(step, "operation_id", "") or "",
        },
    )


def tool_call_lifecycle(
    *,
    call_id: str,
    task_id: str = "",
    tool_name: str = "",
    status: str = "",
    started_at: str = "",
    ended_at: str = "",
    attempt_count: int = 1,
    error_type: str = "",
    error_message: str = "",
    duration_ms: int = 0,
) -> Dict[str, Any]:
    return lifecycle_envelope(
        entity="tool_call",
        entity_id=call_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        attempt_count=attempt_count,
        error_type=error_type,
        error_message=error_message,
        parent_id=task_id,
        extra={"tool_name": tool_name, "duration_ms": int(duration_ms or 0)},
    )


def _append_audit(task, event_type: str, *, status: str = "", payload: Optional[Dict] = None):
    from orchestrator_integration.agent_runtime import append_event

    return append_event(
        task,
        event_type,
        status=status or getattr(task, "status", "") or "",
        payload=payload or {},
    )


def audit_status_transition(
    task,
    *,
    from_status: str,
    to_status: str,
    reason: str = "",
    blocker_code: str = "",
) -> Dict[str, Any]:
    if from_status == to_status:
        return {"skipped": True, "reason": "same_status"}
    return _append_audit(
        task,
        AUDIT_STATUS_TRANSITION,
        status=to_status,
        payload={
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "blocker_code": blocker_code,
            "lifecycle": task_lifecycle(task),
        },
    )


def audit_auth_changed(
    task,
    *,
    prior_scope: Optional[Dict[str, Any]] = None,
    new_scope: Optional[Dict[str, Any]] = None,
    source: str = "continue_merge",
) -> Dict[str, Any]:
    prior_scope = prior_scope or {}
    new_scope = new_scope or {}
    return _append_audit(
        task,
        AUDIT_AUTH_CHANGED,
        payload={
            "source": source,
            "prior_allow": list(prior_scope.get("allow_actions") or []),
            "prior_deny": list(prior_scope.get("deny_actions") or []),
            "new_allow": list(new_scope.get("allow_actions") or []),
            "new_deny": list(new_scope.get("deny_actions") or []),
            "pending": list(new_scope.get("pending_auth_actions") or []),
            "deferred_new_allows": (
                (new_scope.get("continue_auth_policy") or {}).get("deferred_new_allows")
                or []
            ),
        },
    )


def audit_approval_decision(
    task,
    *,
    decision: str,
    interrupt_id: str = "",
    actions: Optional[Sequence[str]] = None,
    actor: str = "",
) -> Dict[str, Any]:
    return _append_audit(
        task,
        AUDIT_APPROVAL_DECISION,
        status=getattr(task, "status", "") or "",
        payload={
            "decision": decision,
            "interrupt_id": interrupt_id,
            "actions": list(actions or []),
            "actor": actor,
        },
    )


def audit_final_delivery(
    task,
    *,
    acceptance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    acceptance = acceptance or getattr(task, "acceptance", None) or {}
    delivery = acceptance.get("delivery") or (getattr(task, "facts", None) or {}).get(
        "delivery_report"
    )
    return _append_audit(
        task,
        AUDIT_FINAL_DELIVERY,
        status=getattr(task, "status", "") or "",
        payload={
            "ok": bool(acceptance.get("ok")),
            "reasons": list(acceptance.get("reasons") or []),
            "delivery_summary": (delivery or {}).get("summary")
            if isinstance(delivery, dict)
            else {},
            "lifecycle": task_lifecycle(task),
        },
    )


def find_hanging_tasks(
    *,
    older_than_seconds: int = 900,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """发现悬挂任务：running 且租约过期或长时间无更新。"""
    from orchestrator_integration.models import AgentTask

    now = timezone.now()
    cutoff = now - timedelta(seconds=max(60, older_than_seconds))
    qs = (
        AgentTask.objects.filter(status__in=["running", "pending", "queued"])
        .order_by("updated_at")[: limit * 3]
    )
    hanging: List[Dict[str, Any]] = []
    for task in qs:
        if len(hanging) >= limit:
            break
        updated = getattr(task, "updated_at", None)
        lease_exp = getattr(task, "lease_expires_at", None)
        lease_expired = bool(lease_exp and lease_exp < now)
        stale = bool(updated and updated < cutoff)
        if not (lease_expired or (stale and (not lease_exp or lease_exp <= now))):
            continue
        hanging.append(
            {
                "task_id": task.public_task_id or str(task.pk),
                "status": task.status,
                "updated_at": updated.isoformat() if updated else "",
                "lease_expires_at": lease_exp.isoformat() if lease_exp else "",
                "lease_expired": lease_expired,
                "stale": stale,
                "lifecycle": task_lifecycle(task),
            }
        )
    return hanging


def compute_ops_metrics(
    *,
    user_id: Optional[int] = None,
    limit: int = 200,
    hanging_older_than_seconds: int = 900,
) -> Dict[str, Any]:
    """运营指标：悬挂 / 续跑成功 / 重复操作 / 完成率 / 平均耗时。"""
    from orchestrator_integration.models import AgentStep, AgentTask, TaskEvent

    qs = AgentTask.objects.all()
    if user_id:
        qs = qs.filter(user_id=user_id)
    recent = list(qs.order_by("-updated_at")[:limit])
    n = len(recent) or 1
    by_status: Dict[str, int] = {}
    completed = 0
    durations: List[float] = []
    for t in recent:
        st = t.status or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        if st == "completed":
            completed += 1
        created = getattr(t, "created_at", None)
        ended = getattr(t, "completed_at", None) or getattr(t, "updated_at", None)
        if created and ended and st in {"completed", "failed", "cancelled", "blocked"}:
            durations.append(max(0.0, (ended - created).total_seconds() * 1000.0))

    task_pks = [t.id for t in recent]
    events = list(
        TaskEvent.objects.filter(task_id__in=task_pks).values(
            "task_id", "event_type", "payload", "status"
        )
    )

    # 续跑：有 resume_plan / continue 事件，且最终 completed 或仍可恢复
    resume_tasks = set()
    resume_success = set()
    for e in events:
        et = e.get("event_type") or ""
        if et in {"resume_plan", "task_resumed", "continuation_budget_topup"} or (
            et == AUDIT_STATUS_TRANSITION
            and isinstance(e.get("payload"), dict)
            and "续跑" in str((e.get("payload") or {}).get("reason") or "")
        ):
            resume_tasks.add(e["task_id"])
    # 也把 facts.resume_plan / budget.resume_count 算入
    for t in recent:
        facts = t.facts or {}
        budget = t.budget or {}
        if facts.get("resume_plan") or budget.get("resume_count"):
            resume_tasks.add(t.id)
        if t.id in resume_tasks and t.status == "completed":
            resume_success.add(t.id)
        # 续跑后 blocked 但有 recovery 不算成功；仅 completed
    resume_total = len(resume_tasks) or 0
    resume_success_rate = (
        round(len(resume_success) / resume_total, 4) if resume_total else None
    )

    # 重复操作：idempotent skip / duplicate_completion / replay blocked
    dup_events = 0
    for e in events:
        et = e.get("event_type") or ""
        pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if et in {"idempotent_replay_blocked", "duplicate_step"}:
            dup_events += 1
        if pl.get("reason") in {
            "idempotent_replay_blocked",
            "duplicate_completion",
        }:
            dup_events += 1
        if (pl.get("acceptance") or {}).get("error_code") == "duplicate_completion":
            dup_events += 1
    # 步骤级：同 idempotency_key 多次
    steps = list(
        AgentStep.objects.filter(task_id__in=task_pks).values(
            "task_id", "evidence", "status", "duration_ms", "attempt_count"
        )
    )
    key_counts: Dict[str, int] = {}
    for s in steps:
        ev = s.get("evidence") if isinstance(s.get("evidence"), dict) else {}
        key = str(ev.get("idempotency_key") or "")
        if key:
            key_counts[key] = key_counts.get(key, 0) + 1
    dup_keys = sum(1 for c in key_counts.values() if c > 1)
    tool_steps = len(steps) or 1
    duplicate_op_rate = round((dup_events + dup_keys) / tool_steps, 4)

    hanging = find_hanging_tasks(
        older_than_seconds=hanging_older_than_seconds, limit=100
    )
    avg_duration_ms = (
        round(sum(durations) / len(durations), 2) if durations else None
    )

    return {
        "tasks_sampled": len(recent),
        "status_counts": by_status,
        "hanging_task_count": len(hanging),
        "hanging_tasks": hanging[:20],
        "resume_attempts": resume_total,
        "resume_success_count": len(resume_success),
        "resume_success_rate": resume_success_rate,
        "duplicate_op_events": dup_events,
        "duplicate_idempotency_keys": dup_keys,
        "duplicate_op_rate": duplicate_op_rate,
        "completion_rate": round(completed / n, 4),
        "avg_duration_ms": avg_duration_ms,
        "duration_samples": len(durations),
    }


def detect_anomalies(
    *,
    user_id: Optional[int] = None,
    hanging_older_than_seconds: int = 900,
) -> Dict[str, Any]:
    """异常检测：悬挂、租约过期、无进展尖峰。"""
    metrics = compute_ops_metrics(
        user_id=user_id,
        hanging_older_than_seconds=hanging_older_than_seconds,
    )
    alerts: List[Dict[str, Any]] = []
    if metrics.get("hanging_task_count", 0) > 0:
        alerts.append(
            {
                "code": "hanging_tasks",
                "severity": "high" if metrics["hanging_task_count"] >= 3 else "medium",
                "message": f"发现 {metrics['hanging_task_count']} 个悬挂任务",
                "count": metrics["hanging_task_count"],
                "sample_task_ids": [
                    h.get("task_id") for h in (metrics.get("hanging_tasks") or [])[:5]
                ],
                "recovery": "运行 reconcile_stale_tasks；或用户发送「继续」恢复。",
            }
        )
    dup_rate = metrics.get("duplicate_op_rate") or 0
    if dup_rate >= 0.25:
        alerts.append(
            {
                "code": "high_duplicate_op_rate",
                "severity": "medium",
                "message": f"重复操作率偏高: {dup_rate}",
                "duplicate_op_rate": dup_rate,
                "recovery": "检查续跑是否误重放；核对 idempotency_key 与副作用守卫。",
            }
        )
    resume_rate = metrics.get("resume_success_rate")
    if (
        resume_rate is not None
        and metrics.get("resume_attempts", 0) >= 3
        and resume_rate < 0.5
    ):
        alerts.append(
            {
                "code": "low_resume_success_rate",
                "severity": "high",
                "message": f"续跑成功率偏低: {resume_rate}",
                "resume_success_rate": resume_rate,
                "recovery": "检查租约争用、证据门禁与环境就绪。",
            }
        )
    return {
        "ok": len(alerts) == 0,
        "alerts": alerts,
        "metrics_snapshot": {
            "hanging_task_count": metrics.get("hanging_task_count"),
            "resume_success_rate": metrics.get("resume_success_rate"),
            "duplicate_op_rate": metrics.get("duplicate_op_rate"),
            "completion_rate": metrics.get("completion_rate"),
            "avg_duration_ms": metrics.get("avg_duration_ms"),
        },
    }


def emit_anomaly_alerts(
    *,
    user_id: Optional[int] = None,
    hanging_older_than_seconds: int = 900,
) -> Dict[str, Any]:
    """检测异常并写入日志；若有代表性任务则落审计事件。"""
    result = detect_anomalies(
        user_id=user_id,
        hanging_older_than_seconds=hanging_older_than_seconds,
    )
    for alert in result.get("alerts") or []:
        logger.warning(
            "anomaly_alert code=%s severity=%s message=%s recovery=%s",
            alert.get("code"),
            alert.get("severity"),
            alert.get("message"),
            alert.get("recovery"),
        )
    # 对首个悬挂任务写审计，便于从 task_id 还原
    hanging = (result.get("metrics_snapshot") or {})
    sample_ids = []
    for a in result.get("alerts") or []:
        sample_ids.extend(a.get("sample_task_ids") or [])
    if sample_ids:
        from orchestrator_integration.models import AgentTask

        tid = sample_ids[0]
        task = (
            AgentTask.objects.filter(public_task_id=str(tid)).first()
            or AgentTask.objects.filter(pk=tid).first()
        )
        if task is not None:
            try:
                _append_audit(
                    task,
                    AUDIT_ANOMALY_ALERT,
                    status=task.status,
                    payload={"alerts": result.get("alerts"), "ok": result.get("ok")},
                )
            except Exception as err:
                logger.debug("anomaly audit skipped: %s", err)
    return result


def heartbeat_and_probe(task, owner: str, *, ttl_minutes: int = 15) -> Dict[str, Any]:
    """运行时心跳：续租并记录探测结果。"""
    from orchestrator_integration.runtime.lease_service import heartbeat_lease

    ok = heartbeat_lease(task, owner, ttl_minutes=ttl_minutes)
    payload = {
        "ok": ok,
        "owner": owner,
        "ttl_minutes": ttl_minutes,
        "lifecycle": task_lifecycle(task),
    }
    if ok:
        try:
            _append_audit(task, AUDIT_HEARTBEAT, status=task.status, payload=payload)
        except Exception:
            pass
    else:
        logger.warning(
            "heartbeat failed task=%s owner=%s",
            getattr(task, "public_task_id", None) or getattr(task, "pk", None),
            owner,
        )
    return payload


def reclaim_stale_tasks(
    *,
    older_than_seconds: int = 900,
    limit: int = 50,
    emit_alerts: bool = True,
) -> Dict[str, Any]:
    """超时回收悬挂任务，并可选发出告警。"""
    from orchestrator_integration.task_state_closure import (
        reconcile_stale_running_tasks,
    )

    before = find_hanging_tasks(older_than_seconds=older_than_seconds, limit=limit)
    result = reconcile_stale_running_tasks(
        older_than_seconds=older_than_seconds, limit=limit
    )
    # 为回收结果补审计
    from orchestrator_integration.models import AgentTask

    for item in result.get("closed") or []:
        tid = item.get("task_id")
        task = AgentTask.objects.filter(public_task_id=str(tid)).first()
        if task is None:
            continue
        try:
            _append_audit(
                task,
                AUDIT_STALE_RECLAIM,
                status=task.status,
                payload={
                    "result": item.get("result"),
                    "blocker_code": "process_interrupted",
                    "lifecycle": task_lifecycle(task),
                },
            )
            audit_status_transition(
                task,
                from_status="running",
                to_status=task.status,
                reason="stale_reclaim",
                blocker_code="process_interrupted",
            )
        except Exception as err:
            logger.debug("stale reclaim audit skipped: %s", err)

    alerts = emit_anomaly_alerts(hanging_older_than_seconds=older_than_seconds) if emit_alerts else {}
    return {
        "hanging_before": len(before),
        "reclaimed": result.get("count") or 0,
        "closed": result.get("closed") or [],
        "alerts": alerts.get("alerts") if isinstance(alerts, dict) else [],
        "reconcile": result,
    }


def trace_task(task_id: str) -> Dict[str, Any]:
    """从任务 ID 还原：DB 状态、事件、步骤、生命周期（验证方法2）。"""
    from orchestrator_integration.models import AgentStep, AgentTask, TaskEvent

    task = (
        AgentTask.objects.filter(public_task_id=str(task_id)).first()
        or AgentTask.objects.filter(pk=task_id).first()
        if str(task_id).isdigit()
        else AgentTask.objects.filter(public_task_id=str(task_id)).first()
    )
    if task is None:
        return {"ok": False, "error": "task_not_found", "task_id": task_id}

    events = list(
        TaskEvent.objects.filter(task=task)
        .order_by("sequence")
        .values(
            "event_id",
            "sequence",
            "event_type",
            "status",
            "step_id",
            "payload",
            "created_at",
        )
    )
    for e in events:
        if e.get("created_at"):
            e["created_at"] = e["created_at"].isoformat()

    steps = []
    for step in AgentStep.objects.filter(task=task).order_by("step_number"):
        steps.append(step_lifecycle(step, task_id=task.public_task_id or str(task.pk)))

    return {
        "ok": True,
        "task_id": task.public_task_id or str(task.pk),
        "db_status": task.status,
        "lifecycle": task_lifecycle(task),
        "acceptance": task.acceptance or {},
        "error_message": task.error_message or "",
        "events": events,
        "steps": steps,
        "event_count": len(events),
        "step_count": len(steps),
        "audit_types_present": sorted(
            {
                e["event_type"]
                for e in events
                if e["event_type"]
                in {
                    AUDIT_STATUS_TRANSITION,
                    AUDIT_AUTH_CHANGED,
                    AUDIT_APPROVAL_DECISION,
                    AUDIT_FINAL_DELIVERY,
                    AUDIT_HEARTBEAT,
                    AUDIT_ANOMALY_ALERT,
                    AUDIT_STALE_RECLAIM,
                    "task_completed",
                    "task_blocked",
                    "stream_closure",
                    "approval_required",
                }
            }
        ),
    }
