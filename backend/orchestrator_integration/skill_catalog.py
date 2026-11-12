"""Skill/Tool Catalog：机器可读的 Skill 目录（分区、副作用、action 契约）。

分区白名单与未登记写操作门禁均以此为源，避免与 bundled_skills 手写漂移。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent / "skill_catalog.json"

_ACTION_RE = re.compile(r"(?i)--action[\s=]+['\"]?(?P<action>[\w-]+)")
_READONLY_ACTION_PREFIX_RE = re.compile(
    r"(?i)^(?:get|list|search|query|find|read|view|show|count|describe|detail|check|fetch"
    r"|download|export|preview|validate|help|stat|stats|collect|extract)(?:[_-]|$)"
)
# execute/run 有执行副作用，不算纯只读（R23）；写入确认路径另有 is_mutating 判定

SideEffect = str  # read | write | irreversible


@dataclass(frozen=True)
class SkillCatalogEntry:
    name: str
    zones: FrozenSet[str]
    side_effect: SideEffect  # read | write | irreversible | execute | unknown
    depends_on: FrozenSet[str] = field(default_factory=frozenset)
    timeout_sec: int = 120
    actions: Dict[str, SideEffect] = field(default_factory=dict)
    # FR06 能力扩展
    idempotent: bool = False
    retry_policy: str = "none"  # none | once | bounded
    reconcile_action: str = ""
    validator: str = ""
    resource_keys: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SkillCatalog:
    version: int
    skills: Dict[str, SkillCatalogEntry]

    def get(self, name: str) -> Optional[SkillCatalogEntry]:
        return self.skills.get((name or "").strip())

    def names_for_zone(self, zone_id: str) -> FrozenSet[str]:
        z = (zone_id or "").strip()
        if not z:
            return frozenset()
        return frozenset(e.name for e in self.skills.values() if z in e.zones)

    def zone_skill_map(self) -> Dict[str, FrozenSet[str]]:
        zones: Dict[str, set] = {}
        for entry in self.skills.values():
            for z in entry.zones:
                zones.setdefault(z, set()).add(entry.name)
        return {k: frozenset(v) for k, v in zones.items()}


def _parse_entry(raw: dict) -> SkillCatalogEntry:
    actions = {
        str(k).strip(): str(v).strip()
        for k, v in (raw.get("actions") or {}).items()
        if k and v
    }
    effect = str(raw.get("side_effect") or raw.get("effect") or "read").strip()
    # execute_testcase / run_api 等不再仅按名称视为纯只读（FR06.1）
    if effect == "execute":
        effect = "execute"
    return SkillCatalogEntry(
        name=str(raw.get("name") or "").strip(),
        zones=frozenset(str(z).strip() for z in (raw.get("zones") or []) if z),
        side_effect=effect,
        depends_on=frozenset(
            str(d).strip() for d in (raw.get("depends_on") or []) if d
        ),
        timeout_sec=int(raw.get("timeout_sec") or 120),
        actions=actions,
        idempotent=bool(raw.get("idempotent", False)),
        retry_policy=str(raw.get("retry_policy") or "none").strip(),
        reconcile_action=str(raw.get("reconcile_action") or "").strip(),
        validator=str(raw.get("validator") or "").strip(),
        resource_keys=frozenset(
            str(k).strip() for k in (raw.get("resource_keys") or []) if k
        ),
    )


@lru_cache(maxsize=1)
def load_skill_catalog(path: Optional[str] = None) -> SkillCatalog:
    catalog_path = Path(path) if path else _CATALOG_PATH
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("加载 Skill Catalog 失败 path=%s err=%s", catalog_path, exc)
        return SkillCatalog(version=0, skills={})

    skills: Dict[str, SkillCatalogEntry] = {}
    for raw in data.get("skills") or []:
        if not isinstance(raw, dict):
            continue
        entry = _parse_entry(raw)
        if entry.name:
            skills[entry.name] = entry
    return SkillCatalog(version=int(data.get("version") or 1), skills=skills)


def reload_skill_catalog() -> SkillCatalog:
    load_skill_catalog.cache_clear()
    return load_skill_catalog()


def extract_command_actions(command: Optional[str]) -> List[str]:
    if not command:
        return []
    return [m.group("action") for m in _ACTION_RE.finditer(str(command))]


def action_looks_mutating(action: str) -> bool:
    return not bool(_READONLY_ACTION_PREFIX_RE.match((action or "").strip()))


def resolve_action_side_effect(
    skill_name: Optional[str], command: Optional[str]
) -> Optional[SideEffect]:
    """解析命令最高副作用；无法判定时返回 None。"""
    catalog = load_skill_catalog()
    entry = catalog.get(skill_name or "")
    actions = extract_command_actions(command)
    if entry and actions and entry.actions:
        levels = []
        for a in actions:
            if a in entry.actions:
                levels.append(entry.actions[a])
            elif action_looks_mutating(a):
                levels.append("write")
            else:
                levels.append("read")
        if "irreversible" in levels:
            return "irreversible"
        if "write" in levels:
            return "write"
        if "execute" in levels:
            return "execute"
        if "unknown" in levels:
            return "unknown"
        return "read"
    if entry and not actions:
        return entry.side_effect
    return None


def catalog_reject_unregistered_write(
    skill_name: Optional[str], command: Optional[str]
) -> Optional[str]:
    """未在 Catalog 登记的写 action 默认拒写；返回拒绝文案或 None。"""
    catalog = load_skill_catalog()
    entry = catalog.get(skill_name or "")
    if not entry or not entry.actions:
        return None
    actions = extract_command_actions(command)
    if not actions:
        return None
    unknown_writes = [
        a for a in actions if a not in entry.actions and action_looks_mutating(a)
    ]
    if not unknown_writes:
        return None
    registered = ", ".join(sorted(entry.actions))
    return (
        f"Action「{', '.join(unknown_writes)}」未在 Skill Catalog 登记为可写操作，已拒绝。"
        f"Skill「{entry.name}」已登记: {registered}。"
        "请改用已登记 action，或先更新 skill_catalog.json。"
    )


def skills_for_zone(zone_id: str) -> FrozenSet[str]:
    return load_skill_catalog().names_for_zone(zone_id)
