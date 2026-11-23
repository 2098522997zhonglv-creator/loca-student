"""续跑 / 新建 / 消歧决策（纯函数，无 DB 副作用）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orchestrator_integration.turn_context import (
    TURN_AMBIGUOUS,
    TURN_CONTINUE,
    TURN_CORRECT,
    TURN_NEW,
    TURN_NONE,
    TURN_SUPPLEMENT,
    is_bare_continue_phrase,
    looks_like_new_execute_command,
)


class ResumeAction(str, Enum):
    CREATE_NEW = "create_new"
    RESUME = "resume"
    AMBIGUOUS = "ambiguous"
    RETURN_TERMINAL = "return_terminal"


@dataclass(frozen=True)
class ResumeDecision:
    action: ResumeAction
    reason: str = ""
    continue_like: bool = False


class ResumePolicy:
    """根据话术与会话开放任务数量决定任务生命周期动作。"""

    @staticmethod
    def decide(
        *,
        goal: str = "",
        turn_relation: str = "",
        open_count: int = 0,
        has_existing: bool = False,
        resume_task_id: str = "",
        terminal_hit: bool = False,
    ) -> ResumeDecision:
        relation = (turn_relation or "").strip()
        goal_text = goal or ""
        new_execute = looks_like_new_execute_command(goal_text)
        bare_continue = is_bare_continue_phrase(goal_text)

        if resume_task_id and terminal_hit and not has_existing:
            return ResumeDecision(
                action=ResumeAction.RETURN_TERMINAL,
                reason="resume_terminal_forbidden",
            )

        force_new = (not resume_task_id) and (
            new_execute or relation in {TURN_NEW, TURN_NONE}
        )
        if force_new:
            return ResumeDecision(
                action=ResumeAction.CREATE_NEW,
                reason="force_new_execute_or_relation",
            )

        if relation == TURN_AMBIGUOUS or (
            open_count >= 2 and not resume_task_id and bare_continue
        ):
            return ResumeDecision(
                action=ResumeAction.AMBIGUOUS,
                reason="multi_open_bare_continue",
            )

        continue_like = bare_continue and not new_execute
        should_resume = bool(has_existing) and (
            relation in {TURN_CONTINUE, TURN_SUPPLEMENT, TURN_CORRECT}
            or continue_like
            or bool(resume_task_id)
        )
        if should_resume:
            return ResumeDecision(
                action=ResumeAction.RESUME,
                reason="explicit_or_bare_continue",
                continue_like=continue_like,
            )

        return ResumeDecision(
            action=ResumeAction.CREATE_NEW,
            reason="default_create",
        )
