"""执行租约：认领 / 续租 / 释放 / 审批停放。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone


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


def acquire_lease(task, owner: str, ttl_minutes: int = 15) -> bool:
    """条件更新获取执行权（FR04.3）。"""
    result = try_claim_execution(task, owner, ttl_minutes=ttl_minutes)
    return bool(result.get("acquired"))


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
    # 延迟导入，避免与 agent_runtime 循环依赖
    from orchestrator_integration.agent_runtime import append_event

    append_event(
        task,
        "approval_required",
        status="awaiting_approval",
        payload={"interrupt_id": interrupt_id or ""},
    )


class LeaseService:
    """面向对象门面；函数式 API 仍可直接调用模块级函数。"""

    claim = staticmethod(try_claim_execution)
    acquire = staticmethod(acquire_lease)
    heartbeat = staticmethod(heartbeat_lease)
    release = staticmethod(release_lease)
    park = staticmethod(park_for_approval)
