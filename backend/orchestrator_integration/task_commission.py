"""任务立项：主类完整授权 / 辅类询问授权 + 高频剧本短路标记。

在 AgentLoop 挂载工具前完成分类，避免全量工具面与越权扩权。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Set

from orchestrator_integration.intent_router import IntentDecision, KbAccessPolicy
from orchestrator_integration.playbooks import (
    PLAYBOOK_BROWSER_ACT,
    PLAYBOOK_PLATFORM_READ,
    PLAYBOOK_PLATFORM_WRITE,
    PLAYBOOK_TESTCASE_EXECUTE,
    PLAYBOOK_TICKET_KB,
    PLAYBOOK_URL_READ,
    Playbook,
)
from orchestrator_integration.skill_zones import (
    ZONE_BROWSER,
    ZONE_EXECUTE,
    ZONE_KB_QA,
    ZONE_PLATFORM,
    ZONE_TICKET_KB,
    ZONE_WEB,
    SkillZone,
)

# 轻量通道：只读/执行；重量通道：强副作用写入
CHANNEL_LIGHT = "light"
CHANNEL_HEAVY = "heavy"


@dataclass
class TaskCommission:
    task_id: str
    zone_id: str
    playbook_id: str
    primary_skills: FrozenSet[str]
    auxiliary_skills: FrozenSet[str]
    short_circuit: bool
    draft_writes: bool
    channel: str
    max_escape_hatches: int = 1
    max_tool_steps: int = 8
    max_light_retries: int = 1
    max_tokens: int = 0
    max_duration_ms: int = 0
    escape_used: int = 0
    tool_steps_used: int = 0
    granted_auxiliary: Set[str] = field(default_factory=set)
    post_condition: str = ""
    last_tool_results: list = field(default_factory=list)

    def effective_allowlist(self) -> Optional[Set[str]]:
        if self.zone_id == ZONE_KB_QA:
            return set()
        if not self.primary_skills and not self.granted_auxiliary:
            return None if self.zone_id == "general" else set()
        return set(self.primary_skills) | set(self.granted_auxiliary)

    def can_escape(self) -> bool:
        return self.escape_used < self.max_escape_hatches

    def budget_remaining(self) -> int:
        return max(0, self.max_tool_steps - self.tool_steps_used)

    def consume_tool_step(self, n: int = 1) -> tuple[bool, str]:
        """消耗工具步数预算；超限则拒绝。"""
        if self.tool_steps_used + n > self.max_tool_steps:
            return (
                False,
                f"工具步数预算已用尽（{self.tool_steps_used}/{self.max_tool_steps}）。"
                "请用已有工具结果尽量交付关键结果（cart/订单标识/截图/错误原文），"
                "禁止要求用户重写指令或点名 Skill；仅当完全无任何结果可汇报时才可 report_task_blocked。",
            )
        self.tool_steps_used += n
        return True, f"steps {self.tool_steps_used}/{self.max_tool_steps}"

    def grant_auxiliary(self, skill_name: str) -> tuple[bool, str]:
        name = (skill_name or "").strip()
        if not name:
            return False, "skill_name 为空"
        if name in self.primary_skills:
            return True, f"「{name}」已在主类授权内"
        if name not in self.auxiliary_skills:
            return (
                False,
                f"「{name}」不在本任务辅类清单，禁止扩展。辅类: "
                + (", ".join(sorted(self.auxiliary_skills)) or "(无)"),
            )
        if not self.can_escape():
            return False, "Escape Hatch 已用尽（最多 1 次），请改派任务或人工托管"
        self.granted_auxiliary.add(name)
        self.escape_used += 1
        return True, f"已授权辅类 Skill「{name}」（escape {self.escape_used}/{self.max_escape_hatches}）"


def _split_primary_aux(
    zone: SkillZone, playbook: Optional[Playbook]
) -> tuple[FrozenSet[str], FrozenSet[str]]:
    preferred = list(playbook.preferred_skills) if playbook else []
    zone_all = set(zone.allowed_skills) if zone.allowed_skills else set()

    if zone.zone_id == ZONE_EXECUTE:
        # 主类只挂核心执行面；其余进辅类（需 Escape Hatch）
        core = ["ui-automation", "loca-stude"]
        primary = frozenset(
            s for s in core if (not zone_all or s in zone_all)
        ) or frozenset({"ui-automation", "loca-stude"})
        aux_candidates = set(preferred or []) | (zone_all or {"api-automation"})
        aux = frozenset(aux_candidates - set(primary))
        return primary, aux

    if zone.zone_id == ZONE_WEB:
        return frozenset({"url-reader"}), frozenset()

    if zone.zone_id == ZONE_BROWSER:
        # 主类强制 playwright-skill：catalog 顺序/哈希切前两名时曾落到
        # browser-use+ui-automation，而 browser-use 会被 prefer_url_reader 拦死。
        primary = frozenset({"playwright-skill"})
        aux_candidates = (
            set(preferred or [])
            | (zone_all or set())
            | {"playwright-cli", "browser-use", "ui-automation"}
        )
        aux = frozenset(aux_candidates - set(primary))
        return primary, aux

    if zone.zone_id == ZONE_TICKET_KB:
        core = ["workorder-knowledge", "playwright-skill"]
        primary = frozenset(
            s for s in core if (not zone_all or s in zone_all)
        ) or frozenset(core)
        aux = frozenset(({"loca-stude"} | (zone_all or set())) - set(primary))
        return primary, aux

    if zone.zone_id == ZONE_PLATFORM:
        default_aux = {"ui-automation", "api-automation", "playwright-skill"}
        primary = frozenset({"loca-stude"})
        base_aux = (zone_all - primary) if zone_all else set()
        aux = frozenset((base_aux | default_aux) - primary)
        return primary, aux

    if zone.allowed_skills:
        skills = list(zone.allowed_skills)
        primary = frozenset(skills[:2])
        aux = frozenset(skills[2:])
        return primary, aux

    return frozenset(), frozenset()


def _post_condition(playbook: Optional[Playbook], zone: SkillZone) -> str:
    if not playbook:
        return ""
    mapping = {
        PLAYBOOK_TESTCASE_EXECUTE: "执行结果已返回且未夹带未确认写入",
        PLAYBOOK_URL_READ: "url-reader 产出可读正文或明确错误原文",
        PLAYBOOK_BROWSER_ACT: "浏览器交互已完成并回报关键结果（cart/订单标识/截图/页面状态）",
        PLAYBOOK_TICKET_KB: "工单字段已汇总；若 upsert 则须用户确认后才写入",
        PLAYBOOK_PLATFORM_WRITE: "仅草稿/确认后写入；未确认不得落库",
        PLAYBOOK_PLATFORM_READ: "只读查询有真实工具结果",
    }
    return mapping.get(playbook.playbook_id, f"zone={zone.zone_id} 完成用户目标且无越权写入")


def commission_task(
    *,
    message: str,
    zone: SkillZone,
    playbook: Optional[Playbook],
    decision: IntentDecision,
    kb_policy: Optional[KbAccessPolicy] = None,
) -> TaskCommission:
    primary, aux = _split_primary_aux(zone, playbook)
    pb_id = playbook.playbook_id if playbook else ""
    short = bool(
        playbook
        and pb_id
        in {
            PLAYBOOK_TESTCASE_EXECUTE,
            PLAYBOOK_URL_READ,
            PLAYBOOK_BROWSER_ACT,
            PLAYBOOK_PLATFORM_READ,
            PLAYBOOK_TICKET_KB,
        }
    )
    # 浏览器交互步数更多（导航+传图+加购+重试），轻量通道放宽预算
    if pb_id == PLAYBOOK_BROWSER_ACT:
        step_cap_override = 28
    else:
        step_cap_override = None
    # 工单沉淀入库 / 平台写入：短路提示仍开，但走重量通道 + 草稿写
    heavy = bool(
        pb_id == PLAYBOOK_PLATFORM_WRITE
        or (
            pb_id == PLAYBOOK_TICKET_KB
            and any(
                h in (message or "")
                for h in ("沉淀", "写入知识库", "upsert", "同步到知识库")
            )
        )
        or (zone.zone_id == ZONE_PLATFORM and decision.primary == "write")
    )
    draft = heavy or pb_id == PLAYBOOK_PLATFORM_WRITE
    channel = CHANNEL_HEAVY if heavy else CHANNEL_LIGHT
    # 轻量短路路径收紧步数；重量路径稍宽但仍有帽
    step_cap = 4 if (short and channel == CHANNEL_LIGHT) else (6 if heavy else 8)
    if step_cap_override is not None:
        step_cap = step_cap_override
    return TaskCommission(
        task_id=uuid.uuid4().hex[:12],
        zone_id=zone.zone_id,
        playbook_id=pb_id,
        primary_skills=primary,
        auxiliary_skills=aux,
        short_circuit=short,
        draft_writes=draft,
        channel=channel,
        max_tool_steps=step_cap,
        post_condition=_post_condition(playbook, zone),
    )


def commission_hint(commission: TaskCommission) -> str:
    lines = [
        "# 任务立项（服务端）",
        f"- task_id: {commission.task_id}",
        f"- zone: {commission.zone_id} playbook: {commission.playbook_id or '(无)'}",
        f"- 通道: {commission.channel} short_circuit={commission.short_circuit} draft_writes={commission.draft_writes}",
        f"- 主类 Skill（完整授权）: {', '.join(sorted(commission.primary_skills)) or '(无)'}",
        f"- 辅类 Skill（Escape Hatch 内部扩权）: {', '.join(sorted(commission.auxiliary_skills)) or '(无)'}",
        f"- Escape Hatch 剩余: {commission.max_escape_hatches - commission.escape_used}",
        f"- 步数预算: {commission.tool_steps_used}/{commission.max_tool_steps}；"
        f"扩权最多 {commission.max_escape_hatches} 次；轻量失败重试≤{commission.max_light_retries}",
        "- Fork-Join：只读可 parallel；写入/不可逆强制串行且 irreversible 置末",
        "- 禁止要求用户点名 Skill、说「执行」、或重写逐步剧本；不够就 request_tool_expansion 或换主类 skill 自愈。",
    ]
    if commission.post_condition:
        lines.append(f"- 退出断言: {commission.post_condition}")
    if commission.short_circuit:
        if commission.playbook_id == PLAYBOOK_BROWSER_ACT:
            lines.append(
                "- 短路只限制 Skill 探索：入口/分支/规则优先知识库预检索或 knowledge_search；"
                "禁止本机扫盘猜 URL；页面结果必须来自真实浏览器，禁止用文档示例冒充本次结果。"
            )
        else:
            lines.append(
                "- 本轮为高频剧本短路：禁止探索主类以外 Skill；按剧本步骤收敛，勿横向漂移。"
            )
    if commission.draft_writes:
        lines.append(
            "- 写入走草稿态：未用户确认前不得真实落库；界面按钮确认，勿要求用户打字。"
        )
    lines.append(
        "- 工具不足时只可调用 request_tool_expansion 一次申请辅类；"
        "仍不足则用已有结果交付，禁止向用户索要新指令。"
    )
    lines.append(
        "- 结束前调用 assert_task_postcondition；失败不得宣称任务完成。"
    )
    return "\n".join(lines)
