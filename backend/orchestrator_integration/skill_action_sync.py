"""从 bundled Skill 工具脚本同步真实 --action 到 Catalog（R01 / D01 / D02）。

以脚本动作声明为源；同步后做一致性检查。旧 Catalog 名称经别名映射，
不得静默保留已失效且无映射的动作。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

SideEffect = str  # read | write | execute | irreversible | unknown

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI_TOOLS = (
    _REPO_ROOT
    / "bundled_skills"
    / "ui-automation-skill"
    / "ui_automation_tools.py"
)
_API_TOOLS = (
    _REPO_ROOT
    / "bundled_skills"
    / "api-automation-skill"
    / "api_automation_tools.py"
)

# 旧 Catalog / 文档名 → 真实动作（兼容，不删校验）
UI_ACTION_ALIASES: Dict[str, str] = {
    "get_ui_cases": "get_testcases",
    "get_case_detail": "get_testcase",
    "list_elements": "get_elements",
    "create_ui_case": "create_testcase",
    "update_ui_case": "update_testcase",
    "delete_ui_case": "delete_testcase",
    "run_testcase": "execute_testcase",
    "batch_run": "get_batch_records",
}

API_ACTION_ALIASES: Dict[str, str] = {
    "list_apis": "list_interfaces",
    "get_api": "get_interface",
    "run_api": "run_interface",
    "create_api": "create_interface",
    "update_api": "update_interface",
    "delete_api": "delete_interface",
}

_READ_PREFIX = re.compile(
    r"^(?:get|list|search|query|find|read|view|show|count|describe|detail|"
    r"check|fetch|download|export|preview|validate|help|stat|stats|collect|"
    r"extract|generate|available|referenced|history|current|tree)",
    re.I,
)
_IRREVERSIBLE = re.compile(r"^(?:delete|remove|cancel|rollback)", re.I)
_EXECUTE = re.compile(
    r"^(?:execute|run|batch_run|quick_debug|sync_now|batch_sync|test_)",
    re.I,
)


def classify_action_side_effect(action: str) -> SideEffect:
    name = (action or "").strip()
    if not name:
        return "unknown"
    if _IRREVERSIBLE.match(name):
        return "irreversible"
    if _EXECUTE.match(name):
        return "execute"
    if _READ_PREFIX.match(name):
        return "read"
    if name.startswith(("create_", "update_", "set_", "add_", "move_", "clone_", "copy_", "duplicate_", "reorder_", "batch_create", "batch_update")):
        return "write"
    return "write"


def discover_ui_actions(tools_path: Optional[Path] = None) -> List[str]:
    path = tools_path or _UI_TOOLS
    text = path.read_text(encoding="utf-8")
    actions: Set[str] = set(
        re.findall(r"action\s*==\s*['\"]([\w-]+)['\"]", text)
    ) | set(re.findall(r"args\.action\s*==\s*['\"]([\w-]+)['\"]", text))
    # ui_automation_tools.main 内 actions = { "name": lambda ... }
    block = re.search(
        r"actions\s*=\s*\{(.*?)\n\s*\}",
        text,
        re.S,
    )
    if block:
        actions |= set(re.findall(r"[\"']([\w-]+)[\"']\s*:", block.group(1)))
    return sorted(actions)


def discover_api_actions(tools_path: Optional[Path] = None) -> List[str]:
    """解析 api_automation_tools._build_actions 的 CRUD + 特殊动作。"""
    path = tools_path or _API_TOOLS
    text = path.read_text(encoding="utf-8")
    actions: Set[str] = set()

    # CRUD_RESOURCES triples
    crud = re.search(r"CRUD_RESOURCES\s*=\s*\[(.*?)\]", text, re.S)
    if crud:
        for singular, plural, *_ in re.findall(
            r"\(\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*'[^']+'\s*,\s*'[^']+'\s*\)",
            crud.group(1),
        ):
            actions.update(
                {
                    f"list_{plural}",
                    f"get_{singular}",
                    f"create_{singular}",
                    f"update_{singular}",
                    f"delete_{singular}",
                }
            )
    ro = re.search(r"READ_ONLY_RESOURCES\s*=\s*\[(.*?)\]", text, re.S)
    if ro:
        for singular, plural, *_ in re.findall(
            r"\(\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*'[^']+'\s*,\s*'[^']+'\s*\)",
            ro.group(1),
        ):
            actions.update({f"list_{plural}", f"get_{singular}"})

    # actions.update({ 'name': ...
    special = re.search(r"actions\.update\(\s*\{(.*?)\}\s*\)", text, re.S)
    if special:
        for name in re.findall(r"['\"]([\w-]+)['\"]\s*:", special.group(1)):
            actions.add(name)

    return sorted(actions)


def build_actions_map(action_names: Iterable[str]) -> Dict[str, SideEffect]:
    return {a: classify_action_side_effect(a) for a in action_names}


def resolve_action_alias(skill_name: str, action: str) -> str:
    name = (skill_name or "").strip()
    act = (action or "").strip()
    if name == "ui-automation":
        return UI_ACTION_ALIASES.get(act, act)
    if name == "api-automation":
        return API_ACTION_ALIASES.get(act, act)
    return act


def consistency_report(
    *,
    skill_name: str,
    catalog_actions: Dict[str, str],
    discovered: Iterable[str],
) -> Dict[str, List[str]]:
    disc = set(discovered)
    cat = set(catalog_actions or {})
    aliases = (
        UI_ACTION_ALIASES
        if skill_name == "ui-automation"
        else API_ACTION_ALIASES
        if skill_name == "api-automation"
        else {}
    )
    # 目录中仅作为别名存在的不算「失效」
    stale = sorted(
        a
        for a in cat
        if a not in disc and aliases.get(a) not in disc and a not in aliases
    )
    missing = sorted(a for a in disc if a not in cat)
    aliased = sorted(a for a in cat if a in aliases)
    return {
        "missing_in_catalog": missing,
        "stale_in_catalog": stale,
        "alias_only": aliased,
    }


def sync_ui_and_api_action_maps() -> Dict[str, Dict[str, SideEffect]]:
    ui = build_actions_map(discover_ui_actions())
    api = build_actions_map(discover_api_actions())
    # 别名也写入 catalog，副作用跟随真实动作
    for alias, real in UI_ACTION_ALIASES.items():
        ui.setdefault(alias, ui.get(real, classify_action_side_effect(real)))
    for alias, real in API_ACTION_ALIASES.items():
        api.setdefault(alias, api.get(real, classify_action_side_effect(real)))
    return {"ui-automation": ui, "api-automation": api}
