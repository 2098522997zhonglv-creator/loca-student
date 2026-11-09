"""域专家子环脚手架：单环仍乱或单域工具过多时启用，缩小提示与工具面。

默认关闭；满足触发条件或环境变量 FORCE_DOMAIN_SPECIALIST=1 时注入专家提示。
真正的多 Agent 进程拆分可后续替换 run_specialist_prep 返回值，执行入口仍走同一 Tool Gateway。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet, Optional, Set

from orchestrator_integration.skill_catalog import load_skill_catalog
from orchestrator_integration.skill_zones import (
    ZONE_BROWSER,
    ZONE_EXECUTE,
    ZONE_GENERAL,
    ZONE_KB_QA,
    ZONE_PLATFORM,
    ZONE_TICKET_KB,
    ZONE_WEB,
    SkillZone,
)

# 单域挂载 skill 数超过该阈值时建议启用专家子环
SPECIALIST_SKILL_THRESHOLD = 8

_DOMAIN_LABELS = {
    ZONE_KB_QA: "知识库问答专家",
    ZONE_TICKET_KB: "工单知识沉淀专家",
    ZONE_PLATFORM: "平台数据专家",
    ZONE_EXECUTE: "用例执行专家",
    ZONE_WEB: "网页阅读专家",
    ZONE_BROWSER: "浏览器自动化专家",
    ZONE_GENERAL: "通用协调专家",
}


@dataclass(frozen=True)
class SpecialistSpec:
    domain: str
    label: str
    allowed_skills: FrozenSet[str]
    goal_hint: str
    active: bool


def _env_force() -> bool:
    return os.environ.get("FORCE_DOMAIN_SPECIALIST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def should_use_specialist(zone: SkillZone, *, force: Optional[bool] = None) -> bool:
    if force is True or (force is None and _env_force()):
        return zone.zone_id != ZONE_KB_QA
    if zone.zone_id in {ZONE_KB_QA, ZONE_GENERAL}:
        return False
    n = len(zone.allowed_skills) if zone.allowed_skills else 0
    # Catalog 中该域登记数量也作为信号
    catalog_n = len(load_skill_catalog().names_for_zone(zone.zone_id))
    return max(n, catalog_n) >= SPECIALIST_SKILL_THRESHOLD


def build_specialist_spec(zone: SkillZone, user_goal: str = "") -> SpecialistSpec:
    catalog = load_skill_catalog()
    allowed = zone.allowed_skills or catalog.names_for_zone(zone.zone_id)
    if zone.zone_id == ZONE_GENERAL:
        allowed = frozenset(catalog.skills.keys())
    label = _DOMAIN_LABELS.get(zone.zone_id, f"{zone.zone_id}专家")
    goal = (user_goal or "").strip()
    goal_hint = (
        f"本轮以「{label}」身份工作；只完成域内目标"
        + (f"：{goal[:200]}" if goal else "。")
        + " 禁止调用域外 Skill；结果用简短摘要交回编排层。"
    )
    active = should_use_specialist(zone)
    return SpecialistSpec(
        domain=zone.zone_id,
        label=label,
        allowed_skills=frozenset(allowed),
        goal_hint=goal_hint,
        active=active,
    )


def specialist_hint(spec: Optional[SpecialistSpec]) -> str:
    if not spec or not spec.active:
        return ""
    skills = ", ".join(sorted(spec.allowed_skills)) if spec.allowed_skills else "(域内目录)"
    return "\n".join(
        [
            f"# 域专家子环（{spec.label}）",
            f"- domain: {spec.domain}",
            f"- 允许 Skill: {skills}",
            f"- {spec.goal_hint}",
            "- 编排层已完成本轮路由；你无需再挑选其它域。",
        ]
    )


def specialist_allowlist(spec: Optional[SpecialistSpec]) -> Optional[Set[str]]:
    """供 get_skill_tools 收紧；未激活返回 None（不额外限制）。"""
    if not spec or not spec.active:
        return None
    if not spec.allowed_skills:
        return set()
    return set(spec.allowed_skills)


def run_specialist_prep(
    zone: SkillZone, user_message: str = ""
) -> tuple[Optional[SpecialistSpec], str, Optional[Set[str]]]:
    """AgentLoop 入口：返回 (spec, hint, allowlist_override)。"""
    spec = build_specialist_spec(zone, user_message)
    if not spec.active:
        return spec, "", None
    return spec, specialist_hint(spec), specialist_allowlist(spec)
