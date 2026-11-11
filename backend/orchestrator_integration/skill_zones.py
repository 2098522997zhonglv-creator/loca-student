"""Skill 分区：按意图挂载可用 Skill 白名单，避免跨区误调。

不做 LLM 语义选 Skill；由意图路由关键字决定分区，再在 execute 入口强制校验 skill_name。
分区白名单优先来自 skill_catalog.json。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Set

from orchestrator_integration.intent_router import (
    INTENT_BROWSER,
    INTENT_PLATFORM,
    INTENT_WEB,
    INTENT_WRITE,
    IntentDecision,
    KbAccessPolicy,
    is_testcase_execute_intent,
    is_ticket_workorder_intent,
)
from orchestrator_integration.skill_catalog import load_skill_catalog

# 分区 ID
ZONE_KB_QA = "kb_qa"
ZONE_TICKET_KB = "ticket_kb"
ZONE_PLATFORM = "platform"
ZONE_EXECUTE = "execute"
ZONE_WEB = "web"
ZONE_BROWSER = "browser"
ZONE_GENERAL = "general"

# Catalog 缺失时的兜底白名单
_FALLBACK_ZONE_SKILLS: dict[str, FrozenSet[str]] = {
    ZONE_KB_QA: frozenset(),
    ZONE_TICKET_KB: frozenset(
        {"workorder-knowledge", "loca-stude", "playwright-skill"}
    ),
    ZONE_PLATFORM: frozenset(
        {"loca-stude", "api-automation", "ui-automation", "playwright-skill"}
    ),
    ZONE_EXECUTE: frozenset({"ui-automation", "loca-stude", "api-automation"}),
    ZONE_WEB: frozenset({"url-reader"}),
    ZONE_BROWSER: frozenset(
        {"playwright-skill", "playwright-cli", "browser-use", "ui-automation"}
    ),
    ZONE_GENERAL: frozenset(),
}


def zone_skills(zone_id: str) -> FrozenSet[str]:
    """从 Catalog 取分区白名单；空则回退内置表。"""
    if zone_id == ZONE_KB_QA:
        return frozenset()
    if zone_id == ZONE_GENERAL:
        return frozenset()
    catalog = load_skill_catalog()
    names = catalog.names_for_zone(zone_id)
    if names:
        return names
    return _FALLBACK_ZONE_SKILLS.get(zone_id, frozenset())


@dataclass(frozen=True)
class SkillZone:
    zone_id: str
    allowed_skills: FrozenSet[str]
    """空 frozenset 且 zone 为 kb_qa 表示禁止执行；GENERAL 空表示不限制。"""
    allow_execute: bool
    reason: str

    def allows(self, skill_name: str) -> bool:
        name = (skill_name or "").strip()
        if not self.allow_execute:
            return False
        if not self.allowed_skills:
            # GENERAL：不限制
            return self.zone_id == ZONE_GENERAL
        return name in self.allowed_skills

    def effective_allowlist(self) -> Optional[Set[str]]:
        """传给 get_skill_tools 的白名单：None=不限制；set=强制校验。"""
        if self.zone_id == ZONE_GENERAL:
            return None
        if not self.allow_execute:
            # 不挂 execute；read 仍可用，不在此卡 skill 名
            return None
        return set(self.allowed_skills)


def correct_misrouted_zone(
    message: str,
    zone: SkillZone,
    decision: IntentDecision,
    kb_policy: Optional[KbAccessPolicy] = None,
) -> SkillZone:
    """有界路由纠正（FR08.6）：分区与已授权浏览器目标冲突时升到 browser，不扩大写权限。"""
    text = (message or "").strip()
    if zone.zone_id == ZONE_BROWSER:
        return zone
    # 已是执行/写/工单区则不抢占
    if zone.zone_id in {ZONE_EXECUTE, ZONE_PLATFORM, ZONE_TICKET_KB}:
        return zone
    from orchestrator_integration.intent_router import (
        _BROWSER_SESSION_FOLLOWUP_RE,
        _INTERACTIVE_SPA_HOST_RE,
        _INTERACTIVE_SITE_HINTS,
        _PURE_STATIC_READ_HINTS,
        _SITE_ACTION_CUES,
        _URL_RE,
    )

    spa = bool(_INTERACTIVE_SPA_HOST_RE.search(text)) and not any(
        h in text for h in _PURE_STATIC_READ_HINTS
    )
    interactive = any(h in text for h in _INTERACTIVE_SITE_HINTS)
    action = (
        any(c in text for c in _SITE_ACTION_CUES)
        or bool(_URL_RE.search(text))
        or bool(_BROWSER_SESSION_FOLLOWUP_RE.search(text))
    )
    session_follow = bool(_BROWSER_SESSION_FOLLOWUP_RE.search(text))
    if spa or session_follow or (interactive and action) or (
        INTENT_BROWSER in decision.intents
    ):
        if zone.zone_id in {ZONE_KB_QA, ZONE_WEB, ZONE_GENERAL}:
            return SkillZone(
                ZONE_BROWSER,
                zone_skills(ZONE_BROWSER),
                allow_execute=True,
                reason=f"route_correct:{zone.zone_id}->browser",
            )
    return zone


def resolve_skill_zone(
    message: str,
    decision: IntentDecision,
    kb_policy: Optional[KbAccessPolicy] = None,
) -> SkillZone:
    text = (message or "").strip()
    zone: SkillZone

    if kb_policy and kb_policy.reason == "testcase_execute":
        from orchestrator_integration.skill_governance import (
            filter_skills_for_execute_zone,
        )

        zone = SkillZone(
            ZONE_EXECUTE,
            frozenset(filter_skills_for_execute_zone(zone_skills(ZONE_EXECUTE))),
            allow_execute=True,
            reason="testcase_execute",
        )
    elif is_testcase_execute_intent(text, decision):
        from orchestrator_integration.skill_governance import (
            filter_skills_for_execute_zone,
        )

        zone = SkillZone(
            ZONE_EXECUTE,
            frozenset(filter_skills_for_execute_zone(zone_skills(ZONE_EXECUTE))),
            allow_execute=True,
            reason="run_testcase",
        )
    elif is_ticket_workorder_intent(text) or (
        kb_policy and kb_policy.reason == "ticket_workorder"
    ):
        zone = SkillZone(
            ZONE_TICKET_KB,
            zone_skills(ZONE_TICKET_KB),
            allow_execute=True,
            reason="ticket_workorder",
        )
    elif INTENT_WRITE in decision.intents:
        # 写区不再并集 ticket：沉淀走 ticket_kb，避免平台写入与工单 Skill 打架
        zone = SkillZone(
            ZONE_PLATFORM,
            zone_skills(ZONE_PLATFORM),
            allow_execute=True,
            reason="write",
        )
    elif INTENT_WEB in decision.intents and INTENT_BROWSER not in decision.intents:
        zone = SkillZone(
            ZONE_WEB,
            zone_skills(ZONE_WEB),
            allow_execute=True,
            reason="web",
        )
    elif INTENT_BROWSER in decision.intents:
        zone = SkillZone(
            ZONE_BROWSER,
            zone_skills(ZONE_BROWSER),
            allow_execute=True,
            reason="browser",
        )
    elif INTENT_PLATFORM in decision.intents:
        zone = SkillZone(
            ZONE_PLATFORM,
            zone_skills(ZONE_PLATFORM),
            allow_execute=True,
            reason="platform",
        )
    elif decision.pure_kb_qa or decision.is_chitchat_or_meta:
        zone = SkillZone(
            ZONE_KB_QA,
            zone_skills(ZONE_KB_QA),
            allow_execute=False,
            reason="kb_qa",
        )
    else:
        zone = SkillZone(
            ZONE_GENERAL,
            zone_skills(ZONE_GENERAL),
            allow_execute=True,
            reason="general",
        )
    return correct_misrouted_zone(text, zone, decision, kb_policy)


def zone_hint(zone: SkillZone) -> str:
    lines = [
        "# Skill 分区（服务端强制）",
        f"- 本轮分区: {zone.zone_id}（{zone.reason}）",
    ]
    if not zone.allow_execute:
        lines.append(
            "- 本轮禁止 execute_skill_script；只做知识库问答。"
            "若用户原话已含 URL/加购/传图/执行用例/落库，应在下一轮由路由自动升区；"
            "禁止要求用户重写「执行 / browser-use / playwright」话术。"
        )
        return "\n".join(lines)

    if zone.allowed_skills:
        names = ", ".join(sorted(zone.allowed_skills))
        lines.append(f"- 本轮仅允许 Skill: {names}。其它 skill_name 会被服务端拒绝。")
    else:
        lines.append("- 本轮未限制 Skill 名称（general）。")
    lines.append(
        "- 禁止跨区调用（例如问答时跑 ui-automation，或执行用例时调 workorder-knowledge）。"
    )
    if zone.zone_id == ZONE_EXECUTE:
        lines.append(
            "- 执行区禁止写入/生成类 action（create/update/delete/set_case_steps）；"
            "越权写入会被服务端拒绝。"
        )
    return "\n".join(lines)
