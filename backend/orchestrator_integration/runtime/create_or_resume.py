"""创建或续跑任务：只做编排，决策归 ResumePolicy，副作用归 Store/Lease。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from orchestrator_integration.runtime.lease_service import acquire_lease
from orchestrator_integration.runtime.resume_policy import ResumeAction, ResumePolicy
from orchestrator_integration.runtime.task_store import TaskStore

# 与历史 DEFAULT_BUDGET 对齐（agent_runtime 仍保留同名常量）
_DEFAULT_BUDGET = {
    "max_tool_steps": 28,
    "used_tool_steps": 0,
    "max_retries": 3,
    "used_retries": 0,
    "max_tokens": 0,
    "used_tokens": 0,
    "max_duration_ms": 0,
    "used_duration_ms": 0,
}


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
    config_overrides: Optional[Dict[str, Any]] = None,
    resume_task_id: str = "",
    store: Optional[TaskStore] = None,
) -> Any:
    """创建新任务或复用同会话未完成任务（继续/补充语义）。"""
    owner = lease_owner or f"worker-{uuid.uuid4().hex[:8]}"
    store = store or TaskStore()
    existing, terminal, open_count = store.resolve_existing(
        session, resume_task_id=resume_task_id or ""
    )

    decision = ResumePolicy.decide(
        goal=goal or "",
        turn_relation=turn_relation or "",
        open_count=open_count,
        has_existing=existing is not None,
        resume_task_id=resume_task_id or "",
        terminal_hit=terminal is not None,
    )

    if decision.action == ResumeAction.RETURN_TERMINAL:
        return terminal

    if decision.action == ResumeAction.AMBIGUOUS:
        return store.mark_ambiguous(existing, open_count=open_count)

    if decision.action == ResumeAction.RESUME:
        task = existing
        if not acquire_lease(task, owner):
            return task
        return store.apply_resume_scope(
            task,
            goal=goal or "",
            relation=(turn_relation or "").strip(),
            continue_like=decision.continue_like,
            auth_scope=auth_scope,
            commission=commission,
        )

    # CREATE_NEW
    return store.create_running_task(
        session=session,
        user=user,
        project_id=project_id,
        goal=goal or "",
        commission=commission,
        auth_scope=auth_scope,
        stage_plan=stage_plan,
        lease_owner=owner,
        config_overrides=config_overrides,
        default_budget=_DEFAULT_BUDGET,
    )
