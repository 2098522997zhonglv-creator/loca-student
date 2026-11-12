"""请求级观测上下文（zone / playbook / mounted tools / 任务立项），供 skill 工具写入 telemetry。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Set

if TYPE_CHECKING:
    from orchestrator_integration.task_commission import TaskCommission


@dataclass
class TelemetryContext:
    zone: str = ""
    playbook: str = ""
    tools_mounted: List[str] = field(default_factory=list)
    session_id: str = ""
    user_id: Optional[int] = None
    specialist_domain: str = ""
    commission: Optional["TaskCommission"] = None
    granted_skills: Set[str] = field(default_factory=set)
    short_circuit: bool = False
    draft_writes: bool = False
    runtime_task_id: Optional[int] = None
    current_stage: str = ""


_CTX: ContextVar[Optional[TelemetryContext]] = ContextVar(
    "tool_telemetry_context", default=None
)


def set_telemetry_context(ctx: Optional[TelemetryContext]) -> None:
    _CTX.set(ctx)


def get_telemetry_context() -> TelemetryContext:
    return _CTX.get() or TelemetryContext()
