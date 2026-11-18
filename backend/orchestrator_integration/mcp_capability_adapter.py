"""将已同步的 MCP 工具描述适配为能力目录项（元数据，非授权）。"""

from __future__ import annotations

import logging
from typing import Iterator, List

from orchestrator_integration.task_orchestrator.capability_catalog import (
    CapabilityDetail,
)

logger = logging.getLogger("orchestrator_integration")


def iter_mcp_capability_details() -> Iterator[CapabilityDetail]:
    """尝试从 MCP 服务注册表拉取工具描述；无 MCP 或失败时不产出。"""
    try:
        tools = _load_mcp_tool_descriptors()
    except Exception as exc:
        logger.debug("mcp descriptors unavailable: %s", exc)
        return
    for t in tools:
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        yield CapabilityDetail(
            capability_id=f"mcp:{name}",
            source="mcp",
            purpose=str(t.get("description") or t.get("title") or name)[:300],
            when="MCP 工具可用且任务节点选中",
            side_effect=str(t.get("side_effect") or "unknown"),
            available=bool(t.get("available", True)),
            skill_name="",
            idempotent=bool(t.get("idempotent", False)),
            inputs=list(t.get("input_names") or []),
            outputs=str(t.get("output_hint") or "MCP 调用结果"),
            raw=dict(t),
        )


def _load_mcp_tool_descriptors() -> List[dict]:
    """兼容多种注册入口；均不存在则返回空列表。"""
    # 1) 可选 settings 钩子
    try:
        from django.conf import settings

        hook = getattr(settings, "MCP_CAPABILITY_LIST_HOOK", None)
        if callable(hook):
            data = hook()
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass

    # 2) 可选本地模块
    try:
        from orchestrator_integration import mcp_services  # type: ignore

        if hasattr(mcp_services, "list_tool_descriptors"):
            data = mcp_services.list_tool_descriptors()
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass

    return []
