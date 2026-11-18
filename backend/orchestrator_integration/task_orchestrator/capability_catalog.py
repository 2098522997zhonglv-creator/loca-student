"""FR01：统一 MCP / Skill / 内置工具能力目录（适配，不另维护人工白名单）。"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional

from orchestrator_integration.skill_catalog import load_skill_catalog

logger = logging.getLogger("orchestrator_integration")

# 内置工具摘要（来源元数据，非授权指令）
_BUILTIN_CAPS: Dict[str, Dict[str, Any]] = {
    "knowledge_search": {
        "purpose": "检索知识库获取入口、规则与环境说明",
        "when": "入口/规则未知且任务依赖知识库事实",
        "inputs": ["query", "knowledge_base_id"],
        "outputs": "检索片段与来源",
        "side_effect": "read",
        "idempotent": True,
    },
    "read_skill_content": {
        "purpose": "按需加载已选 Skill 的完整说明",
        "when": "候选能力已选定且本任务版本尚未读过",
        "inputs": ["skill_name"],
        "outputs": "Skill 正文",
        "side_effect": "read",
        "idempotent": True,
    },
    "execute_skill_script": {
        "purpose": "执行已挂载 Skill 脚本/命令",
        "when": "当前节点一层/二层候选已授权",
        "inputs": ["skill_name", "command"],
        "outputs": "脚本 stdout/stderr 与退出码",
        "side_effect": "unknown",
        "idempotent": False,
    },
    "request_executor_heal": {
        "purpose": "同目标等价执行器切换（保留产物）",
        "when": "主执行器失败且存在已授权等价工具",
        "inputs": ["from_skill", "to_skill", "reason"],
        "outputs": "切换结果",
        "side_effect": "execute",
        "idempotent": False,
    },
    "assert_task_postcondition": {
        "purpose": "声明任务后置条件是否满足",
        "when": "准备结束任务前",
        "inputs": ["had_unconfirmed_write"],
        "outputs": "通过/失败说明",
        "side_effect": "read",
        "idempotent": True,
    },
    "report_task_blocked": {
        "purpose": "报告不可恢复阻塞与已完成项",
        "when": "所有可用路径失败或缺授权/缺关键参数",
        "inputs": ["reason", "gap"],
        "outputs": "阻塞摘要",
        "side_effect": "read",
        "idempotent": True,
    },
}


@dataclass
class CapabilitySummary:
    capability_id: str
    source: str  # skill | builtin | mcp
    purpose: str
    when: str
    side_effect: str
    available: bool = True
    skill_name: str = ""
    idempotent: bool = True
    zones: tuple = ()
    actions: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def brief(self) -> str:
        return f"{self.capability_id}: {self.purpose}"


@dataclass
class CapabilityDetail(CapabilitySummary):
    inputs: List[str] = field(default_factory=list)
    outputs: str = ""
    retry_policy: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=4)
def load_capability_catalog(version_token: str = "v1") -> Dict[str, CapabilityDetail]:
    """构建统一目录；version_token 变化时刷新缓存。"""
    _ = version_token
    catalog: Dict[str, CapabilityDetail] = {}

    for cid, meta in _BUILTIN_CAPS.items():
        catalog[cid] = CapabilityDetail(
            capability_id=cid,
            source="builtin",
            purpose=str(meta.get("purpose") or ""),
            when=str(meta.get("when") or ""),
            side_effect=str(meta.get("side_effect") or "unknown"),
            available=True,
            skill_name="",
            idempotent=bool(meta.get("idempotent", True)),
            inputs=list(meta.get("inputs") or []),
            outputs=str(meta.get("outputs") or ""),
        )

    skill_cat = load_skill_catalog()
    for name, entry in (skill_cat.skills or {}).items():
        actions = tuple(sorted((entry.actions or {}).keys()))
        purpose = f"Skill「{name}」域能力"
        if name == "ui-automation":
            purpose = "结构化 UI 用例构建与在线执行（动作以 ui_automation_tools 为准）"
        elif name == "api-automation":
            purpose = "接口定义/场景/套件执行（动作以 api_automation_tools ACTIONS 为准）"
        elif name == "playwright-skill":
            purpose = "主浏览器交互、上传、截图与业务断言取证"
        elif name == "task-orchestrator":
            purpose = "任务编排：计划、三层选工具、连续调度与交付"
        catalog[name] = CapabilityDetail(
            capability_id=name,
            source="skill",
            purpose=purpose,
            when=f"zones={','.join(entry.zones or ())}",
            side_effect=str(entry.side_effect or "unknown"),
            available=True,
            skill_name=name,
            idempotent=bool(getattr(entry, "idempotent", False)),
            zones=tuple(entry.zones or ()),
            actions=actions,
            inputs=["command", "action"],
            outputs="Skill 脚本结果；执行类须含本次 execution_id/record_id",
            retry_policy=str(getattr(entry, "retry_policy", "") or ""),
            raw={
                "timeout_sec": getattr(entry, "timeout_sec", None),
                "depends_on": list(getattr(entry, "depends_on", None) or []),
                "action_count": len(actions),
            },
        )

    # MCP：若运行时有已同步服务描述则并入（失败则跳过，不阻断）
    try:
        from orchestrator_integration.mcp_capability_adapter import (
            iter_mcp_capability_details,
        )

        for detail in iter_mcp_capability_details():
            if detail.capability_id and detail.capability_id not in catalog:
                catalog[detail.capability_id] = detail
    except Exception as exc:
        logger.debug("MCP capability adapter skip: %s", exc)

    return catalog


def reload_capability_catalog() -> Dict[str, CapabilityDetail]:
    load_capability_catalog.cache_clear()
    from orchestrator_integration.skill_catalog import reload_skill_catalog

    reload_skill_catalog()
    return load_capability_catalog()


def list_capability_summaries(
    *,
    only_available: bool = True,
    source: Optional[str] = None,
) -> List[CapabilitySummary]:
    cat = load_capability_catalog()
    out: List[CapabilitySummary] = []
    for cap in cat.values():
        if only_available and not cap.available:
            continue
        if source and cap.source != source:
            continue
        out.append(
            CapabilitySummary(
                capability_id=cap.capability_id,
                source=cap.source,
                purpose=cap.purpose,
                when=cap.when,
                side_effect=cap.side_effect,
                available=cap.available,
                skill_name=cap.skill_name,
                idempotent=cap.idempotent,
                zones=cap.zones,
                actions=cap.actions,
            )
        )
    return out


def get_capability(capability_id: str) -> Optional[CapabilityDetail]:
    return load_capability_catalog().get((capability_id or "").strip())


def brief_catalog_for_prompt(limit: int = 40) -> str:
    """FR01：先给模型简短摘要，禁止每节点全量读 Skill。"""
    lines = ["# 能力目录摘要（元数据，非授权）"]
    for i, cap in enumerate(list_capability_summaries()):
        if i >= limit:
            lines.append(f"... 其余 {len(list_capability_summaries()) - limit} 项按需 get_capability")
            break
        lines.append(f"- [{cap.source}] {cap.brief()} (effect={cap.side_effect})")
    return "\n".join(lines)


def capability_exists(capability_id: str) -> bool:
    return get_capability(capability_id) is not None
