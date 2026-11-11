"""
内置工具管理模块

提供 AI Agent 可调用的持久化工具，包括：
- Skill 脚本执行工具（执行用户上传的 Python 脚本）

注意：Diagram 工具（display_diagram, edit_diagram）已移至 loca_stude-Tools MCP
"""

from .meta_tools import get_meta_tools
from .skill_tools import get_skill_tools

import logging

logger = logging.getLogger('orchestrator_integration')


def get_builtin_tools(
    user_id: int,
    project_id: int,
    test_case_id: int = None,
    chat_session_id: str = None,
    user_write_approved: bool = None,
    allowed_skill_names: set = None,
    allow_execute: bool = True,
    include_meta_tools: bool = True,
) -> list:
    """获取所有内置工具。

    allowed_skill_names / allow_execute：由 Skill 分区/任务立项传入。
    include_meta_tools：Escape Hatch / 降级元工具（小工具面常驻）。
    """
    tools = []

    skill_tools = get_skill_tools(
        user_id=user_id,
        project_id=project_id,
        test_case_id=test_case_id,
        chat_session_id=chat_session_id,
        user_write_approved=user_write_approved,
        allowed_skill_names=allowed_skill_names,
        allow_execute=allow_execute,
    )
    tools.extend(skill_tools)
    if include_meta_tools and allow_execute:
        meta = get_meta_tools()
        tools.extend(meta)
        logger.info("[BuiltinTools] 加载 %s 个元工具(Escape Hatch)", len(meta))
    logger.info(
        "[BuiltinTools] 加载 %s 个 Skill 工具 allow_execute=%s allowlist=%s",
        len(skill_tools),
        allow_execute,
        sorted(allowed_skill_names) if allowed_skill_names is not None else None,
    )

    return tools
