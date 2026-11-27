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
        """启用备用能力或（旧路径）申请辅类扩展。

        新编排路径（task-orchestrator）：在已有授权内自动启用二层备用，不向用户扩权。
        旧路径：Escape Hatch，每任务最多 1 次，skill 须在辅类清单内。

        Args:
            skill_name: 要启用的备用 / 辅类 Skill 名
            reason: 简短原因（主工具失败、缺条件、核验等）
        """
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        commission = ctx.commission

        # 新编排路径：request_tool_expansion 适配为二层自动启用（不另维护 Escape 授权）
        orch = getattr(ctx, "orchestrator_mount", None)
        if orch is not None:
            from orchestrator_integration.task_orchestrator.runtime_bridge import (
                try_activate_fallback_from_expansion,
            )

            ok, detail, new_mount = try_activate_fallback_from_expansion(
                orch, skill_name, reason=reason or ""
            )
            record_tool_telemetry(
                zone=ctx.zone,
                playbook=ctx.playbook,
                tool_name="request_tool_expansion",
                skill_name=skill_name,
                success=ok,
                error_kind="" if ok else "fallback_denied",
                attribution="planning",
                tools_mounted=ctx.tools_mounted,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
            if ok:
                ctx.orchestrator_mount = new_mount
                allow = set(new_mount.skill_allowlist) | {skill_name}
                # 并入而非覆盖：保留已有主类/获批 Skill
                ctx.granted_skills = set(ctx.granted_skills or []) | allow
                if commission is not None:
                    try:
                        if hasattr(commission, "grant_skills"):
                            allow = set(commission.grant_skills(allow) or allow)
                        else:
                            commission.granted_auxiliary = set(
                                commission.granted_auxiliary or []
                            ) | {skill_name}
                            commission.primary_skills = frozenset(
                                set(commission.primary_skills or []) | allow
                            )
                            allow = set(commission.effective_allowlist() or allow)
                    except Exception:
                        pass
                # 同轮重绑定：commission + mount + 任务有效授权一并更新
                try:
                    from orchestrator_integration.task_orchestrator.node_runtime import (
                        apply_remount_to_context,
                    )

                    apply_remount_to_context(allow | {skill_name})
                    allow = set(ctx.granted_skills or []) | allow | {skill_name}
                except Exception as remount_err:
                    logger.warning(
                        "[request_tool_expansion] remount/persist failed: %s",
                        remount_err,
                    )
                    # 兜底：至少写任务有效授权
                    try:
                        from orchestrator_integration.models import AgentTask
                        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
                            persist_granted_skills,
                        )

                        task = None
                        if getattr(ctx, "runtime_task_id", None):
                            task = AgentTask.objects.filter(
                                pk=ctx.runtime_task_id
                            ).first()
                        if task is not None:
                            persisted = persist_granted_skills(
                                task, allow | {skill_name}, commission=commission
                            )
                            allow = set(persisted.get("allowed_skills") or allow)
                    except Exception as persist_err:
                        logger.warning(
                            "[request_tool_expansion] persist grant failed: %s",
                            persist_err,
                        )
                logger.info(
                    "[request_tool_expansion] orch L2 skill=%s reason=%s allow=%s",
                    skill_name,
                    (reason or "")[:120],
                    sorted(allow),
                )
                return json.dumps(
                    {
                        "status": "granted",
                        "layer": 2,
                        "message": detail,
                        "skill_name": skill_name,
                        "effective_allowlist": sorted(allow),
                        "note": (
                            "二层备用已写入任务有效授权；请立即重试刚才被拦截的 "
                            "read_skill_content / execute_skill_script 步骤，"
                            "无需用户发送「继续」。"
                        ),
                        "retry_blocked_step": True,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "denied",
                    "layer": 2,
                    "message": detail,
                    "degrade": (
                        "备用能力无法在已有授权内启用。"
                        "若确需超授权动作，请说明对象与影响并等待三层批准；"
                        "禁止换工具绕过拒绝。"
                    ),
                    "skill_name": skill_name,
                },
                ensure_ascii=False,
            )

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
            # 同步 allowlist 到上下文 + 任务持久化，供后续 execute 校验
            allow = commission.effective_allowlist() or set()
            ctx.granted_skills = set(ctx.granted_skills or []) | set(allow)
            try:
                from orchestrator_integration.task_orchestrator.node_runtime import (
                    apply_remount_to_context,
                )

                apply_remount_to_context({skill_name} | set(allow))
                allow = set(ctx.granted_skills or []) | set(allow) | {skill_name}
            except Exception as remount_err:
                logger.warning(
                    "[request_tool_expansion] escape remount failed: %s",
                    remount_err,
                )
                try:
                    from orchestrator_integration.models import AgentTask
                    from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
                        persist_granted_skills,
                    )

                    task = None
                    if getattr(ctx, "runtime_task_id", None):
                        task = AgentTask.objects.filter(
                            pk=ctx.runtime_task_id
                        ).first()
                    if task is not None:
                        persisted = persist_granted_skills(
                            task, {skill_name} | set(allow), commission=commission
                        )
                        allow = set(persisted.get("allowed_skills") or allow)
                except Exception as persist_err:
                    logger.warning(
                        "[request_tool_expansion] persist escape grant failed: %s",
                        persist_err,
                    )
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
                    "note": (
                        "授权已持久化；请立即重试刚才被拦截的步骤，"
                        "无需用户发送「继续」。"
                    ),
                    "retry_blocked_step": True,
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
        facts: dict = {}
        stage_plan: list = []
        target_url = ""
        auth_scope = {}
        task = getattr(ctx, "task", None)
        if task is None and getattr(ctx, "task_id", None):
            try:
                from orchestrator_integration.models import AgentTask

                task = AgentTask.objects.filter(task_id=ctx.task_id).first()
            except Exception:
                task = None
        if task is not None:
            facts = dict(getattr(task, "facts", None) or {})
            stage_plan = list(getattr(task, "stage_plan", None) or [])
            auth_scope = getattr(task, "auth_scope", None) or {}
            target_url = str(
                (getattr(task, "task_scope", None) or {}).get("target_url") or ""
            )
        verdict = evaluate_post_condition(
            post_condition=commission.post_condition,
            tool_results=list(commission.last_tool_results),
            had_unconfirmed_write=bool(had_unconfirmed_write),
            playbook_id=commission.playbook_id,
            auth_scope=auth_scope,
            target_url=target_url,
            stage_plan=stage_plan,
            facts=facts,
            evidence_epoch=facts.get("evidence_epoch"),
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
    def request_executor_heal(
        from_skill: str,
        to_skill: str,
        reason: str = "",
    ) -> str:
        """RC03：在已授权的等价执行器组内有界切换（不扩权、不改 deny）。

        仅当当前执行器不可用且备用在同组且已授权时使用。禁止要求用户改路由话术。
        """
        from orchestrator_integration.action_auth import (
            record_heal_switch,
            resolve_equivalent_executor,
        )
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        commission = ctx.commission
        allowed = set()
        if commission is not None:
            allowed = set(commission.effective_allowlist() or set())
        allowed |= set(ctx.granted_skills or set())
        # 与 skill 门禁一致：读任务持久化授权（扩权后同轮/跨轮）
        try:
            if getattr(ctx, "runtime_task_id", None):
                task_for_auth = AgentTask.objects.filter(
                    pk=ctx.runtime_task_id
                ).first()
                if task_for_auth is not None:
                    auth = dict(getattr(task_for_auth, "auth_scope", None) or {})
                    facts = dict(getattr(task_for_auth, "facts", None) or {})
                    allowed |= {
                        str(s).strip()
                        for s in (auth.get("allowed_skills") or [])
                        if s
                    }
                    allowed |= {
                        str(s).strip()
                        for s in (facts.get("granted_auxiliary") or [])
                        if s
                    }
                    allowed |= {
                        str(s).strip()
                        for s in (facts.get("activated_fallbacks") or [])
                        if s
                    }
        except Exception:
            pass
        resolved = resolve_equivalent_executor(
            current=from_skill,
            candidates=[to_skill],
            allowed_skills=allowed,
        )
        if not resolved.get("ok"):
            record_tool_telemetry(
                zone=ctx.zone,
                tool_name="request_executor_heal",
                skill_name=to_skill,
                success=False,
                error_kind="no_authorized_equivalent",
                attribution="planning",
                session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
            return json.dumps(
                {
                    "status": "denied",
                    "message": resolved.get("message"),
                    "hint": "请在已授权能力内重试或 report_task_blocked；禁止让用户重写指令。",
                },
                ensure_ascii=False,
            )
        count = 0
        if ctx.runtime_task_id:
            task = AgentTask.objects.filter(pk=ctx.runtime_task_id).first()
            if task is not None:
                rec = record_heal_switch(
                    task,
                    from_skill=from_skill,
                    to_skill=to_skill,
                    reason=reason or "executor_unavailable",
                )
                count = int(rec.get("count") or 0)
        record_tool_telemetry(
            zone=ctx.zone,
            tool_name="request_executor_heal",
            skill_name=to_skill,
            success=True,
            error_kind="",
            attribution="planning",
            session_id=ctx.session_id,
            user_id=ctx.user_id,
        )
        return json.dumps(
            {
                "status": "switched",
                "from": from_skill,
                "to": to_skill,
                "reason": reason or "equivalent_authorized_executor",
                "heal_switch_count": count,
                "message": f"已切换到已授权等价执行器 {to_skill}（第 {count} 次自愈）。",
            },
            ensure_ascii=False,
        )

    @langchain_tool
    def report_task_blocked(summary: str, missing_capability: str = "") -> str:
        """任务无法由当前工具面完成时的降级出口（草稿/人工托管说明）。

        仅当策略已用尽或确认技术不可达时调用。若你已想出可执行改法（换序/换上传方式），
        必须先同轮继续 execute_skill_script 自愈，禁止用本工具代替「下一步请续跑」。

        Args:
            summary: 已收集的参数与进展摘要
            missing_capability: 缺少的能力/权限说明
        """
        from orchestrator_integration.execute_flow_guard import (
            PREMATURE_BLOCKED_MSG,
            analyze_execute_progress,
            is_premature_blocked,
        )
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import record_tool_telemetry

        ctx = get_telemetry_context()
        commission = ctx.commission
        prior = list(getattr(commission, "last_tool_results", None) or []) if commission else []
        # 摘要里常含 case_id / get_testcases，一并计入进展判定
        progress = analyze_execute_progress(
            prior + [summary or "", missing_capability or ""]
        )
        if (ctx.zone or "").strip() == "execute" and is_premature_blocked(progress):
            record_tool_telemetry(
                zone=ctx.zone,
                playbook=ctx.playbook,
                tool_name="report_task_blocked",
                skill_name="",
                success=False,
                error_kind="premature_blocked",
                attribution="planning",
                session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
            logger.info(
                "[report_task_blocked] premature reject discovery=%s has_cases=%s",
                progress.get("discovery_hits"),
                progress.get("has_cases"),
            )
            return json.dumps(
                {
                    "status": "rejected",
                    "error_code": "premature_blocked",
                    "message": PREMATURE_BLOCKED_MSG,
                    "summary": (summary or "").strip()[:2000],
                    "missing_capability": (missing_capability or "").strip()[:500],
                    "next": (
                        "UI 有用例 → execute_testcase --wait_result；"
                        "UI 为空 → playwright 按功能步骤实测（可 knowledge_search 取入口 URL）。"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
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
                "若 summary 里仍写着可执行的「下一步续跑」步骤，说明过早调用了本工具——"
                "下次应先同轮自愈执行那些步骤。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return [
        request_tool_expansion,
        request_executor_heal,
        assert_task_postcondition,
        report_task_blocked,
    ]
