"""请求级观测上下文（zone / playbook / mounted tools），供 skill 工具写入 telemetry。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TelemetryContext:
    zone: str = ""
    playbook: str = ""
    tools_mounted: List[str] = field(default_factory=list)
    session_id: str = ""
    user_id: Optional[int] = None
    specialist_domain: str = ""


_CTX: ContextVar[Optional[TelemetryContext]] = ContextVar(
    "tool_telemetry_context", default=None
)


def set_telemetry_context(ctx: Optional[TelemetryContext]) -> None:
    _CTX.set(ctx)


def get_telemetry_context() -> TelemetryContext:
    return _CTX.get() or TelemetryContext()
