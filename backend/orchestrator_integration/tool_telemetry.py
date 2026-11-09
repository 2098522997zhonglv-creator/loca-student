"""工具调用结构化观测：zone / skill / 成败 / 耗时 / 白名单拒绝。"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RING: Deque["ToolCallTelemetry"] = deque(maxlen=2000)
_TELEMETRY_LOG = (
    Path(__file__).resolve().parents[2] / "data" / "logs" / "tool_telemetry.jsonl"
)


@dataclass
class ToolCallTelemetry:
    ts: float
    zone: str = ""
    playbook: str = ""
    tool_name: str = ""
    skill_name: str = ""
    success: bool = True
    duration_ms: float = 0.0
    rejected_by_allowlist: bool = False
    rejected_by_catalog: bool = False
    error_kind: str = ""
    tools_mounted: List[str] = field(default_factory=list)
    session_id: str = ""
    user_id: Optional[int] = None

    def to_log_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.ts))
        return d


def record_tool_telemetry(
    *,
    zone: str = "",
    playbook: str = "",
    tool_name: str = "",
    skill_name: str = "",
    success: bool = True,
    duration_ms: float = 0.0,
    rejected_by_allowlist: bool = False,
    rejected_by_catalog: bool = False,
    error_kind: str = "",
    tools_mounted: Optional[List[str]] = None,
    session_id: str = "",
    user_id: Optional[int] = None,
) -> ToolCallTelemetry:
    event = ToolCallTelemetry(
        ts=time.time(),
        zone=zone or "",
        playbook=playbook or "",
        tool_name=tool_name or "",
        skill_name=skill_name or "",
        success=success,
        duration_ms=float(duration_ms or 0.0),
        rejected_by_allowlist=bool(rejected_by_allowlist),
        rejected_by_catalog=bool(rejected_by_catalog),
        error_kind=error_kind or "",
        tools_mounted=list(tools_mounted or []),
        session_id=session_id or "",
        user_id=user_id,
    )
    with _LOCK:
        _RING.append(event)
    payload = event.to_log_dict()
    logger.info(
        "tool_telemetry zone=%s playbook=%s tool=%s skill=%s ok=%s ms=%.1f "
        "allowlist_reject=%s catalog_reject=%s kind=%s",
        event.zone,
        event.playbook,
        event.tool_name,
        event.skill_name,
        event.success,
        event.duration_ms,
        event.rejected_by_allowlist,
        event.rejected_by_catalog,
        event.error_kind,
    )
    try:
        _TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _TELEMETRY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("写入 tool_telemetry.jsonl 失败: %s", exc)
    return event


def recent_telemetry(limit: int = 200) -> List[ToolCallTelemetry]:
    with _LOCK:
        items = list(_RING)
    if limit <= 0:
        return items
    return items[-limit:]


def aggregate_telemetry(limit: int = 2000) -> Dict[str, Any]:
    """聚合近期调用：Top 误调、慢路径、分区命中。"""
    events = recent_telemetry(limit=limit)
    if not events:
        # 尝试从 jsonl 补读
        events = _load_jsonl(limit)

    zone_hits = Counter(e.zone or "unknown" for e in events)
    allowlist_rejects = Counter(
        f"{e.zone}:{e.skill_name or e.tool_name}"
        for e in events
        if e.rejected_by_allowlist
    )
    catalog_rejects = Counter(
        f"{e.skill_name}:{e.error_kind or 'catalog'}"
        for e in events
        if e.rejected_by_catalog
    )
    slow = sorted(
        (
            {
                "zone": e.zone,
                "skill_name": e.skill_name,
                "tool_name": e.tool_name,
                "duration_ms": e.duration_ms,
                "playbook": e.playbook,
            }
            for e in events
            if e.duration_ms > 0
        ),
        key=lambda x: x["duration_ms"],
        reverse=True,
    )[:10]
    fail_kinds = Counter(e.error_kind for e in events if e.error_kind)
    playbooks = Counter(e.playbook for e in events if e.playbook)

    return {
        "total": len(events),
        "zone_hits": dict(zone_hits.most_common()),
        "top_allowlist_rejects": dict(allowlist_rejects.most_common(10)),
        "top_catalog_rejects": dict(catalog_rejects.most_common(10)),
        "top_slow_calls": slow,
        "error_kinds": dict(fail_kinds.most_common(10)),
        "playbook_hits": dict(playbooks.most_common()),
    }


def _load_jsonl(limit: int) -> List[ToolCallTelemetry]:
    path = _TELEMETRY_LOG
    if not path.exists():
        return []
    out: List[ToolCallTelemetry] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(
                ToolCallTelemetry(
                    ts=float(d.get("ts") or time.time()),
                    zone=d.get("zone") or "",
                    playbook=d.get("playbook") or "",
                    tool_name=d.get("tool_name") or "",
                    skill_name=d.get("skill_name") or "",
                    success=bool(d.get("success", True)),
                    duration_ms=float(d.get("duration_ms") or 0),
                    rejected_by_allowlist=bool(d.get("rejected_by_allowlist")),
                    rejected_by_catalog=bool(d.get("rejected_by_catalog")),
                    error_kind=d.get("error_kind") or "",
                    tools_mounted=list(d.get("tools_mounted") or []),
                    session_id=d.get("session_id") or "",
                    user_id=d.get("user_id"),
                )
            )
    except Exception as exc:
        logger.debug("读取 tool_telemetry.jsonl 失败: %s", exc)
    return out


class TelemetryTimer:
    """with TelemetryTimer() as t: ... ; 用 t.elapsed_ms 取当前耗时。"""

    def __init__(self) -> None:
        self._start = 0.0
        self.ms = 0.0

    def __enter__(self) -> "TelemetryTimer":
        self._start = time.perf_counter()
        return self

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0

    def __exit__(self, *args) -> None:
        self.ms = self.elapsed_ms
