"""元工具：Escape Hatch / 降级草稿说明（逃生舱，限 1 次扩权）。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.tools import tool as langchain_tool

logger = logging.getLogger("orchestrator_integration")


def get_meta_tools() -> list:
    """挂载到小工具面：允许申请辅类扩展，禁止无限扩权。"""

    @langchain_tool
    def request_tool_expansion(skill_name: str, reason: str = "") -> str:
        """申请扩展本轮辅类 Skill（Escape Hatch，每任务最多 1 次）。

        仅当主类工具确实无法完成任务时调用。skill_name 必须落在任务立项的辅类清单内。
        扩权用尽或 skill 不在辅类 → 应改为向用户说明并生成人工/草稿方案，禁止继续乱试。

        Args:
            skill_name: 要申请的辅类 Skill 名
            reason: 简短原因（为何主类不够）
        """
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        commission = ctx.commission
        if commission is None:
            msg = "当前无任务立项上下文，无法扩权。请在明确任务下重试。"
            record_tool_telemetry(
                zone=ctx.zone,
                tool_name="request_tool_expansion",
                skill_name=skill_name,
                success=False,
                error_kind="no_commission",
                attribution="planning",
            )
            return msg

        ok, detail = commission.grant_auxiliary(skill_name)
        record_tool_telemetry(
            zone=commission.zone_id,
            playbook=commission.playbook_id,
            tool_name="request_tool_expansion",
            skill_name=skill_name,
            success=ok,
            error_kind="" if ok else "escape_denied",
            attribution="planning",
            tools_mounted=ctx.tools_mounted,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
        )
        if ok:
            # 同步 allowlist 到上下文，供后续 execute 校验
            allow = commission.effective_allowlist() or set()
            ctx.granted_skills = set(allow)
            logger.info(
                "[request_tool_expansion] granted skill=%s task=%s escape=%s reason=%s",
                skill_name,
                commission.task_id,
                commission.escape_used,
                (reason or "")[:120],
            )
            return json.dumps(
                {
                    "status": "granted",
                    "message": detail,
                    "skill_name": skill_name,
                    "effective_allowlist": sorted(allow),
                    "escape_used": commission.escape_used,
                },
                ensure_ascii=False,
            )

        logger.info(
            "[request_tool_expansion] denied skill=%s task=%s detail=%s",
            skill_name,
            commission.task_id,
            detail,
        )
        return json.dumps(
            {
                "status": "denied",
                "message": detail,
                "degrade": (
                    "请停止继续试工具。向用户说明缺口，整理已掌握参数，"
                    "建议人工处理或下一条消息明确授权辅类/写入。"
                ),
                "skill_name": skill_name,
            },
            ensure_ascii=False,
        )

    @langchain_tool
    def assert_task_postcondition(had_unconfirmed_write: bool = False) -> str:
        """对任务退出条件做确定性断言（不靠模型自评）。

        在即将结束回复前调用；失败则应 report_task_blocked 或继续补齐，不得宣称已完成。

        Args:
            had_unconfirmed_write: 本轮是否仍有未确认写入挂起
        """
        from orchestrator_integration.execution_policy import evaluate_post_condition
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        commission = ctx.commission
        if commission is None:
            return json.dumps(
                {"ok": False, "reasons": ["无任务立项"]}, ensure_ascii=False
            )
        verdict = evaluate_post_condition(
            post_condition=commission.post_condition,
            tool_results=list(commission.last_tool_results),
            had_unconfirmed_write=bool(had_unconfirmed_write),
            playbook_id=commission.playbook_id,
        )
        record_tool_telemetry(
            zone=commission.zone_id,
            playbook=commission.playbook_id,
            tool_name="assert_task_postcondition",
            success=bool(verdict.get("ok")),
            error_kind="" if verdict.get("ok") else "postcondition_failed",
            attribution="" if verdict.get("ok") else "planning",
            session_id=ctx.session_id,
            user_id=ctx.user_id,
        )
        return json.dumps(verdict, ensure_ascii=False, indent=2)

    @langchain_tool
    def report_task_blocked(summary: str, missing_capability: str = "") -> str:
        """任务无法由当前工具面完成时的降级出口（草稿/人工托管说明）。

        在 Escape Hatch 用尽或确认无匹配工具时调用，禁止继续无差别重试。

        Args:
            summary: 已收集的参数与进展摘要
            missing_capability: 缺少的能力/权限说明
        """
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        record_tool_telemetry(
            zone=ctx.zone,
            playbook=ctx.playbook,
            tool_name="report_task_blocked",
            skill_name="",
            success=True,
            error_kind="degraded_draft",
            attribution="planning",
            session_id=ctx.session_id,
            user_id=ctx.user_id,
        )
        payload = {
            "status": "degraded",
            "mode": "draft_human_handoff",
            "summary": (summary or "").strip()[:2000],
            "missing_capability": (missing_capability or "").strip()[:500],
            "message": (
                "任务受阻：把下列进展与技术阻塞原样告知用户，不要再调用执行工具。"
                "禁止要求用户重写指令、说「执行」、点名 Skill，或二选一确认目标。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return [request_tool_expansion, assert_task_postcondition, report_task_blocked]
