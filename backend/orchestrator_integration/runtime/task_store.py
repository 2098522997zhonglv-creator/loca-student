"""AgentTask 持久化读写（无决策）。"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django.utils import timezone

OPEN_STATUSES = (
    "pending",
    "running",
    "awaiting_approval",
    "blocked",
    "paused",
    "cancelling",
)
TERMINAL_STATUSES = frozenset({"cancelled", "completed", "failed"})


def _new_public_id() -> str:
    return uuid.uuid4().hex[:16]


def _extract_url(text: str) -> str:
    import re

    m = re.search(r"https?://[^\s]+", text or "", re.I)
    return m.group(0) if m else ""


class TaskStore:
    """会话维度的任务查询与写入。"""

    def open_queryset(self, session):
        from orchestrator_integration.models import AgentTask

        return AgentTask.objects.filter(session=session, status__in=OPEN_STATUSES)

    def resolve_existing(
        self, session, *, resume_task_id: str = ""
    ) -> Tuple[Any, Optional[Any], int]:
        """返回 (existing_open_or_none, terminal_if_resume_hit, executable_open_count)。"""
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.turn_context import (
            _task_to_prior,
            executable_open_tasks,
        )

        open_qs = self.open_queryset(session)
        rows = list(open_qs.order_by("-updated_at"))
        executable = executable_open_tasks(
            [_task_to_prior(row) for row in rows]
        )
        executable_ids = {item.task_pk for item in executable}
        open_count = len(executable_ids)
        existing = None
        terminal = None
        if resume_task_id:
            existing = (
                next(
                    (row for row in rows if str(row.public_task_id) == str(resume_task_id)),
                    None,
                )
                or AgentTask.objects.filter(
                    pk=resume_task_id, status__in=OPEN_STATUSES
                ).first()
            )
            if existing is None:
                terminal = (
                    AgentTask.objects.filter(public_task_id=resume_task_id).first()
                    or AgentTask.objects.filter(pk=resume_task_id).first()
                )
                if terminal is not None and str(terminal.status) not in TERMINAL_STATUSES:
                    terminal = None
        else:
            # Prefer the most recent actionable task. A newer KB-only shell
            # must not mask the single resumable browser/test task.
            existing = next(
                (row for row in rows if int(row.pk) in executable_ids),
                rows[0] if rows else None,
            )
        return existing, terminal, open_count

    def mark_ambiguous(self, task, *, open_count: int) -> Any:
        if task is None:
            return None
        scope = dict(task.task_scope or {})
        scope["ambiguous_resume"] = True
        scope["open_task_count"] = open_count
        task.task_scope = scope
        task.save(update_fields=["task_scope", "updated_at"])
        return task

    def apply_resume_scope(
        self,
        task,
        *,
        goal: str,
        relation: str,
        continue_like: bool,
        auth_scope: Optional[Dict[str, Any]],
        commission,
    ) -> Any:
        """续跑路径：写 scope / 合并授权 / 预算续额（不含租约）。"""
        from orchestrator_integration.turn_context import (
            TURN_CONTINUE,
            TURN_CORRECT,
            TURN_SUPPLEMENT,
            extract_constraint_patch,
        )

        # 延迟导入避免循环
        from orchestrator_integration.agent_runtime import (
            apply_goal_correction,
            topup_budget_for_user_continuation,
        )

        scope = dict(task.task_scope or {})
        scope.pop("ambiguous_resume", None)
        if not scope.get("raw_goal"):
            scope["raw_goal"] = task.goal or ""
        msg = (goal or "")[:2000]
        if relation == TURN_CORRECT:
            apply_goal_correction(task, msg)
            scope = dict(task.task_scope or {})
            scope["correction_message"] = msg
        elif relation == TURN_SUPPLEMENT or (
            not continue_like and relation != TURN_CONTINUE
        ):
            scope["supplement_message"] = msg
            constraints = dict(scope.get("constraints") or {})
            constraints.update(extract_constraint_patch(msg))
            if constraints:
                scope["constraints"] = constraints
        else:
            scope["continue_message"] = msg
            constraints = dict(scope.get("constraints") or {})
            constraints.update(extract_constraint_patch(msg))
            if constraints:
                scope["constraints"] = constraints
        task.task_scope = scope
        if auth_scope:
            merged = dict(task.auth_scope or {})
            allow = set(merged.get("allow_actions") or [])
            deny = set(merged.get("deny_actions") or [])
            allow |= set(auth_scope.get("allow_actions") or [])
            deny |= set(auth_scope.get("deny_actions") or [])
            allow -= deny
            merged["allow_actions"] = sorted(allow)
            merged["deny_actions"] = sorted(deny)
            for k, v in auth_scope.items():
                if k not in {"allow_actions", "deny_actions"} and k not in merged:
                    merged[k] = v
            task.auth_scope = merged
        pb_id = ""
        if commission is not None:
            pb_id = str(getattr(commission, "playbook_id", "") or "")
        topup = topup_budget_for_user_continuation(task, playbook_id=pb_id)
        if topup.get("tool_topup") or topup.get("token_topup"):
            scope = dict(task.task_scope or {})
            scope["continuation_budget_topup"] = topup
            task.task_scope = scope
        task.save(
            update_fields=[
                "task_scope",
                "auth_scope",
                "budget",
                "max_steps",
                "updated_at",
                "lease_owner",
                "lease_expires_at",
                "status",
            ]
        )
        return task

    def create_running_task(
        self,
        *,
        session,
        user,
        project_id: Optional[int],
        goal: str,
        commission,
        auth_scope: Optional[Dict[str, Any]],
        stage_plan: Optional[List[Dict[str, Any]]],
        lease_owner: str,
        config_overrides: Optional[Dict[str, Any]],
        default_budget: Dict[str, Any],
    ) -> Any:
        from orchestrator_integration.models import AgentTask, AgentBlackboard
        from orchestrator_integration.task_execution_config import (
            budget_from_effective,
            build_effective_config,
            validate_config_overrides,
        )
        from orchestrator_integration.agent_runtime import append_event

        now = timezone.now()
        stages = stage_plan or []
        ov: Dict[str, Any] = {}
        if config_overrides:
            checked = validate_config_overrides(
                config_overrides if isinstance(config_overrides, dict) else {}
            )
            if checked.get("ok"):
                ov = checked.get("overrides") or {}
        effective = build_effective_config(
            stage_plan=stages,
            auth_scope=auth_scope or {},
            commission=commission,
            overrides=ov,
        )
        try:
            from orchestrator_integration.message_locale import infer_response_locale

            explicit_locale = ""
            if isinstance(ov, dict):
                explicit_locale = str(ov.get("response_locale") or "")
            budget_locale = infer_response_locale(goal or "", explicit=explicit_locale)
            effective["response_locale"] = budget_locale
        except Exception:
            effective.setdefault("response_locale", "auto")
        budget = budget_from_effective(effective)
        budget["response_locale"] = effective.get("response_locale", "auto")
        for k, v in (default_budget or {}).items():
            budget.setdefault(k, v)
        public_id = getattr(commission, "task_id", None) or _new_public_id()
        current_stage = stages[0]["stage_id"] if stages else ""
        merged_auth = dict(auth_scope or {})
        merged_auth.update(effective.get("auth_scope") or {})
        task = AgentTask.objects.create(
            session=session,
            project_id=project_id,
            user=user,
            public_task_id=public_id,
            goal=goal or "",
            status="running",
            max_steps=int(
                budget.get("max_tool_actions") or budget.get("max_tool_steps") or 28
            ),
            plan_version=1,
            current_stage=current_stage,
            stage_plan=stages,
            auth_scope=merged_auth,
            task_scope={
                "target_url": _extract_url(goal),
                "raw_goal": (goal or "")[:2000],
                "config_hash": budget.get("config_hash", ""),
                "schema_version": budget.get("schema_version", 1),
            },
            budget=budget,
            facts={},
            lease_owner=lease_owner,
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
