"""底层 Skill 门禁：分区动作策略、跨 Skill 亲和、越权写入拦截。

不靠页面可观测性约束；在工具入口强制识别与拒写/拒跨区。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set

from orchestrator_integration.skill_catalog import (
    catalog_reject_unregistered_write,
    extract_command_actions,
    resolve_action_side_effect,
)
from orchestrator_integration.skill_zones import (
    ZONE_BROWSER,
    ZONE_EXECUTE,
    ZONE_KB_QA,
    ZONE_PLATFORM,
    ZONE_TICKET_KB,
    ZONE_WEB,
)

# 浏览器 Skill：页面实测/登录/拖拽，不是平台 create_ui_*/add_testcase 写入
_BROWSER_SKILLS = frozenset(
    {"playwright-skill", "playwright-cli", "browser-use"}
)

# 同批 commands 内允许共存的 skill 亲和组（组外混用 → 跨 Skill 打架）
_SKILL_AFFINITY: dict[str, frozenset[str]] = {
    "platform_core": frozenset({"loca-stude", "api-automation", "ui-automation"}),
    "ticket": frozenset({"workorder-knowledge", "loca-stude", "playwright-skill"}),
    "web": frozenset({"url-reader"}),
    "browser": frozenset(
        {"playwright-skill", "playwright-cli", "browser-use", "ui-automation"}
    ),
}

_WRITE_INTENT_RE = re.compile(
    r"写入|保存|落库|入库|生成|创建|添加|新建|删除|更新|确认写入|同意写入"
)


def affinity_groups_for_skill(skill_name: str) -> Set[str]:
    name = (skill_name or "").strip()
    return {gid for gid, members in _SKILL_AFFINITY.items() if name in members}


def reject_cross_skill_batch(commands: Sequence[dict]) -> Optional[str]:
    """同一批 commands 若跨亲和组，拒绝（避免多 Skill 打架）。"""
    names: List[str] = []
    for c in commands:
        if not isinstance(c, dict):
            continue
        n = (c.get("skill_name") or "").strip()
        if n:
            names.append(n)
    uniq = sorted(set(names))
    if len(uniq) <= 1:
        return None

    # 所有 skill 必须共享至少一个亲和组
    group_sets = [affinity_groups_for_skill(n) for n in uniq]
    if any(not g for g in group_sets):
        # 未分组的 skill：只允许与同名共存（已在 uniq<=1 处理），异名则拒
        return (
            f"批量 commands 含未分组或不兼容 Skill: {', '.join(uniq)}。"
            "请拆成同域分批调用，避免跨 Skill 打架。"
        )
    shared = set.intersection(*group_sets)
    if shared:
        return None
    return (
        f"批量 commands 跨 Skill 亲和组混用: {', '.join(uniq)}。"
        "请按域拆批（平台 / 工单 / 网页 / 浏览器），禁止一锅烩。"
    )


def elevate_zone_for_authorized_execute(
    zone_id: str,
    *,
    authorized_skills: Optional[Set[str]] = None,
    skill_name: Optional[str] = None,
    mount_execute: bool = False,
) -> str:
    """知识库问答区若已挂执行通道或已获批执行类 Skill，提升为可执行分区。

    启用知识库 ≠ 禁止执行：只要执行工具已挂载或已有获批执行类 Skill，
    kb_qa 分类禁令即失效（白名单/动作授权仍单独约束）。
    """
    zone = (zone_id or "").strip()
    if zone != ZONE_KB_QA:
        return zone
    auth = {str(s).strip() for s in (authorized_skills or set()) if str(s).strip()}
    name = (skill_name or "").strip()
    browserish = (auth & _BROWSER_SKILLS) or (name in _BROWSER_SKILLS)
    platformish = bool(
        auth & {"loca-stude", "ui-automation", "api-automation"}
    ) or name in {"loca-stude", "ui-automation", "api-automation"}
    # 执行通道已挂载：一律离开 kb_qa（能进 execute_skill_script 即属此类）
    if mount_execute or auth:
        if browserish:
            return ZONE_BROWSER
        if platformish or name or auth:
            return ZONE_EXECUTE
        return ZONE_EXECUTE
    return zone


def reject_zone_privilege_escalation(
    *,
    zone_id: str,
    skill_name: Optional[str],
    command: Optional[str],
    authorized_skills: Optional[Set[str]] = None,
    mount_execute: bool = False,
) -> Optional[str]:
    """分区越权：执行区禁止写入；问答区默认禁止 execute；网页区禁非 url-reader。

    若 authorized_skills / mount_execute 表明本轮已获批执行通道，kb_qa 不再一刀切拒绝。
    """
    name = (skill_name or "").strip()
    zone = elevate_zone_for_authorized_execute(
        zone_id or "",
        authorized_skills=authorized_skills,
        skill_name=name,
        mount_execute=mount_execute,
    )
    if not zone:
        return None

    if zone == ZONE_KB_QA:
        return (
            "本轮为知识库问答区，禁止 execute_skill_script。"
            "请用知识库回答；禁止要求用户重写指令或点名 Skill。"
            "若任务需要页面实测，请先 request_tool_expansion 获批执行类 Skill；"
            "获批后同轮重试须能通过本门禁。"
        )

    if zone == ZONE_WEB and name and name != "url-reader":
        return (
            f"本轮为网页阅读区，仅允许 url-reader；拒绝 Skill「{name}」。"
        )

    if zone == ZONE_EXECUTE:
        # UI 自动化为空时的浏览器实测兜底：playwright 无 --action，catalog 标 write
        # 也不得按「平台写入」拦截（页面操作 ≠ create_ui_*/落库）
        if name in _BROWSER_SKILLS:
            return None
        # 未登记写 action
        unreg = catalog_reject_unregistered_write(name, command)
        if unreg:
            return unreg
        effect = resolve_action_side_effect(name, command)
        actions = extract_command_actions(command)
        # 无 catalog 时用只读前缀启发：非 read 即拒
        if effect in ("write", "irreversible"):
            return (
                "本轮为用例执行区，禁止写入/生成类 action（越权）。"
                f"当前 skill={name} actions={actions or ['(无--action)']}。"
                "请只使用 execute_testcase / run_testcase / get_*；"
                "UI 用例为空时可用 playwright-skill 做页面实测；"
                "若要生成 UI 或落库，请明确「确认写入」后走确认按钮。"
            )
        if effect is None and actions:
            # 有 action 但不在 catalog：执行区仅放行 execute/run/get/list…
            from orchestrator_integration.builtin_tools.skill_tools import (
                is_mutating_skill_command,
            )

            if is_mutating_skill_command(command, name):
                return (
                    "本轮为用例执行区，禁止写入类命令（越权）。"
                    f"skill={name} command 含写入语义。"
                )
        if effect is None and not actions:
            from orchestrator_integration.builtin_tools.skill_tools import (
                is_mutating_skill_command,
            )

            if is_mutating_skill_command(command, name):
                return (
                    "本轮为用例执行区，禁止写入类命令（越权）。"
                    f"skill={name}"
                )

    if zone in {ZONE_PLATFORM, ZONE_TICKET_KB, ZONE_BROWSER}:
        unreg = catalog_reject_unregistered_write(name, command)
        if unreg:
            return unreg

    return None


def message_has_explicit_write_intent(message: str) -> bool:
    return bool(_WRITE_INTENT_RE.search(message or ""))


def filter_skills_for_execute_zone(allowed: Iterable[str]) -> Set[str]:
    """执行区白名单：去掉工单沉淀类，避免与执行打架。"""
    block = {"workorder-knowledge", "url-reader", "weknora-kb"}
    return {s for s in allowed if s and s not in block}
