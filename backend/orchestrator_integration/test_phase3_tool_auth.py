"""阶段 3 验收：工具与授权编排 — 分级、继续不扩权、缺环境即 blocked。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


class Phase3ToolAuthAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 阶段3验收结果 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def _task(self, **kwargs):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        n = AgentTask.objects.count()
        user = User.objects.create_user(username=f"p3_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"p3-sess-{n}", title="p3"
        )
        defaults = dict(
            session=chat,
            user=user,
            public_task_id=f"p3{n:08d}",
            goal="下单与查询混合任务",
            status="running",
            current_stage="run_or_browser",
            stage_plan=[
                {
                    "stage_id": "query_cases",
                    "tool_capabilities": ["knowledge_search"],
                },
                {
                    "stage_id": "run_or_browser",
                    "tool_capabilities": ["loca-stude"],
                    "auth_actions": ["platform_write"],
                },
                {
                    "stage_id": "assert_and_deliver",
                    "tool_capabilities": ["assert_task_postcondition"],
                },
            ],
            auth_scope={"allow_actions": ["platform_write"], "deny_actions": []},
            facts={"completed_stage_ids": ["query_cases"]},
        )
        defaults.update(kwargs)
        return AgentTask.objects.create(**defaults)

    def test_v1_step_manifest_lists_tools_inputs_outputs(self):
        """验证方法1：任务开始前可生成步骤清单（工具/授权/验收）。"""
        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
            build_step_tool_manifest,
            classify_tool_risk,
        )

        task = self._task()
        manifest = build_step_tool_manifest(
            stage_plan=task.stage_plan,
            auth_scope=task.auth_scope,
        )
        steps = manifest.get("steps") or []
        ok = len(steps) >= 3 and any(
            "loca-stude" in (s.get("tools") or []) for s in steps
        )
        hint_ok = "工具与授权编排" in (manifest.get("hint") or "")
        risk = classify_tool_risk(
            skill_name="loca-stude",
            command="python x.py --action submit_order --sku A",
        )
        self._record(
            "P3-V1-manifest",
            ok and hint_ok and risk.get("requires_l3_approval") is True,
            f"steps={len(steps)} hint={hint_ok} risk={risk.get('risk')}",
        )

    def test_v2_continue_does_not_silent_expand_allow(self):
        """验证方法2：「继续」新 allow 进入 pending，不静默扩权。"""
        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
            authorize_tool_call,
            merge_auth_scope_for_continue,
        )

        prior = {"allow_actions": ["platform_write"], "deny_actions": []}
        patch = {"allow_actions": ["platform_write", "submit_order", "pay"]}
        merged = merge_auth_scope_for_continue(
            prior, patch, require_l3_for_new_allows=True
        )
        allow = set(merged.get("allow_actions") or [])
        pending = set(merged.get("pending_auth_actions") or [])
        no_silent = "submit_order" not in allow and "pay" not in allow
        pending_ok = {"submit_order", "pay"} <= pending

        # pending 未批准 → 不可执行
        denied = authorize_tool_call(
            auth_scope=merged,
            skill_name="loca-stude",
            command="python x.py --action submit_order",
        )
        # 已授权 write 可执行
        allowed = authorize_tool_call(
            auth_scope=merged,
            skill_name="loca-stude",
            command="python x.py --action platform_write --name t1",
        )
        self._record(
            "P3-V2-no-silent-expand",
            no_silent
            and pending_ok
            and denied.get("ok") is False
            and allowed.get("ok") is True,
            f"allow={sorted(allow)} pending={sorted(pending)} "
            f"denied={denied.get('error_code')} allowed={allowed.get('ok')}",
        )

    def test_v3_deny_and_read_risk_boundaries(self):
        """验证方法3：deny 拦截；只读工具可无授权调用。"""
        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
            authorize_tool_call,
            classify_tool_risk,
        )

        scope = {
            "allow_actions": ["platform_write"],
            "deny_actions": ["pay"],
        }
        denied = authorize_tool_call(
            auth_scope=scope,
            skill_name="checkout",
            command="python pay.py --action pay",
        )
        read_ok = authorize_tool_call(
            auth_scope={},
            skill_name="knowledge_search",
            command="",
        )
        read_risk = classify_tool_risk(skill_name="knowledge_search")
        self._record(
            "P3-V3-deny-and-read",
            denied.get("ok") is False
            and read_ok.get("ok") is True
            and read_risk.get("callable_without_auth") is True,
            f"deny_code={denied.get('error_code')} read={read_ok.get('reason')} "
            f"risk={read_risk.get('risk')}",
        )

    def test_v4_missing_env_blocks_immediately(self):
        """验证方法4：缺 Skill/执行器 → 立即 blocked + 恢复路径。"""
        from orchestrator_integration.task_orchestrator.env_readiness import (
            block_task_for_missing_env,
            probe_required_capabilities,
        )

        task = self._task(status="running")
        probe = probe_required_capabilities(
            required_skills=["__missing_skill_xyz__"],
            mount_execute=True,
            executor_mounted=False,
            skill_names_available=[],
            browser_required=False,
        )
        self.assertFalse(probe.get("ok"))
        result = block_task_for_missing_env(task, probe)
        task.refresh_from_db()
        facts = task.facts or {}
        env = facts.get("env_readiness") or {}
        acceptance = result.get("acceptance") or {}
        recovery = acceptance.get("recovery") or ""
        self._record(
            "P3-V4-env-blocked",
            result.get("action") == "blocked"
            and task.status == "blocked"
            and env.get("ok") is False
            and "继续" in recovery,
            f"status={task.status} missing={env.get('missing')} recovery={recovery[:80]}",
        )

    def test_v5_ready_probe_does_not_block(self):
        """验证方法5：能力齐全时 probe ok，不改任务状态。"""
        from orchestrator_integration.task_orchestrator.env_readiness import (
            block_task_for_missing_env,
            probe_required_capabilities,
            required_skills_from_manifest,
        )
        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
            build_step_tool_manifest,
        )

        task = self._task(status="running")
        manifest = build_step_tool_manifest(
            stage_plan=task.stage_plan, auth_scope=task.auth_scope
        )
        # 模拟已安装所需 skill
        names = required_skills_from_manifest(manifest)
        probe = probe_required_capabilities(
            required_skills=[n for n in names if n == "loca-stude"],
            mount_execute=True,
            executor_mounted=True,
            skill_names_available=["loca-stude"],
        )
        result = block_task_for_missing_env(task, probe)
        task.refresh_from_db()
        self._record(
            "P3-V5-ready",
            probe.get("ok") is True
            and result.get("action") == "ready"
            and task.status == "running",
            f"probe_ok={probe.get('ok')} action={result.get('action')} status={task.status}",
        )

    def test_v6_fallback_skills_do_not_hard_block(self):
        """fallback 缺失不硬拦；仅 primary 缺失才 blocked。"""
        from orchestrator_integration.task_orchestrator.env_readiness import (
            probe_required_capabilities,
            required_primary_skills_for_probe,
        )

        manifest = {
            "steps": [
                {
                    "step_id": "run_or_browser",
                    "tools": ["loca-stude", "playwright-cli", "url-reader"],
                }
            ]
        }
        plan = {
            "nodes": [
                {
                    "node_id": "run_or_browser",
                    "primary_candidates": ["loca-stude"],
                    "fallback_candidates": ["playwright-cli", "url-reader"],
                }
            ]
        }
        split = required_primary_skills_for_probe(
            manifest,
            current_stage="run_or_browser",
            task_plan=plan,
        )
        probe = probe_required_capabilities(
            required_skills=split["primary"],
            optional_skills=split["optional"],
            mount_execute=True,
            executor_mounted=True,
            skill_names_available=["loca-stude"],  # fallback 都不在
        )
        self._record(
            "P3-V6-fallback-soft",
            split["primary"] == ["loca-stude"]
            and set(split["optional"]) >= {"playwright-cli", "url-reader"}
            and probe.get("ok") is True
            and len(probe.get("optional_missing") or []) >= 2,
            f"primary={split['primary']} optional={split['optional']} "
            f"ok={probe.get('ok')} opt_miss={probe.get('optional_missing')}",
        )

    def test_v7_expansion_propagates_to_skill_gate(self):
        """获批 Skill 写入有效授权后，read/execute 门放行；未获批仍拦截；无需用户「继续」。"""
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools
        from orchestrator_integration.skill_zones import ZONE_KB_QA
        from orchestrator_integration.task_commission import TaskCommission
        from orchestrator_integration.task_orchestrator.node_runtime import (
            apply_remount_to_context,
        )
        from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
            persist_granted_skills,
            restore_granted_skills_from_task,
        )
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        task = self._task(
            status="running",
            auth_scope={"allow_actions": [], "deny_actions": []},
            facts={},
        )
        # 复现线上：kb_qa 区主类为空，扩权前全部 Skill 被拦
        commission = TaskCommission(
            task_id="p3-expand",
            zone_id=ZONE_KB_QA,
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(
                {"playwright-skill", "browser-use", "loca-stude"}
            ),
            short_circuit=False,
            draft_writes=False,
            channel="light",
        )
        self.assertEqual(commission.effective_allowlist(), set())

        tools = {
            t.name: t
            for t in get_skill_tools(
                user_id=1,
                project_id=1,
                allowed_skill_names=set(),  # 创建时空白名单（closed-over）
                allow_execute=True,
            )
        }
        set_telemetry_context(
            TelemetryContext(
                zone=ZONE_KB_QA,
                commission=commission,
                runtime_task_id=task.pk,
                granted_skills=set(),
            )
        )
        try:
            denied_before = tools["read_skill_content"].invoke(
                {"skill_name": "playwright-skill"}
            )
            before_blocked = "不在本任务主类授权内" in denied_before or "辅类" in denied_before

            # 模拟 request_tool_expansion granted → remount + persist
            apply_remount_to_context({"playwright-skill"})
            persisted = persist_granted_skills(
                task, {"playwright-skill"}, commission=commission
            )
            task.refresh_from_db()
            allow_after = commission.effective_allowlist() or set()
            auth_skills = set((task.auth_scope or {}).get("allowed_skills") or [])
            facts_g = set((task.facts or {}).get("granted_auxiliary") or [])

            ok_after = tools["read_skill_content"].invoke(
                {"skill_name": "playwright-skill"}
            )
            still_blocked = (
                "不在本任务主类授权内" in ok_after
                or "辅类，尚未授权" in ok_after
                or "不在本轮分区白名单" in ok_after
            )

            # 未获批等价执行器仍拦截
            denied_other = tools["read_skill_content"].invoke(
                {"skill_name": "browser-use"}
            )
            other_blocked = (
                "不在本任务主类授权内" in denied_other
                or "辅类" in denied_other
                or "尚未授权" in denied_other
            )

            # 跨轮恢复：新 commission 从任务读回授权
            commission2 = TaskCommission(
                task_id="p3-expand-2",
                zone_id=ZONE_KB_QA,
                playbook_id="",
                primary_skills=frozenset(),
                auxiliary_skills=frozenset({"playwright-skill", "browser-use"}),
                short_circuit=False,
                draft_writes=False,
                channel="light",
            )
            restored = restore_granted_skills_from_task(task, commission=commission2)
            restore_ok = "playwright-skill" in restored and (
                "playwright-skill" in (commission2.effective_allowlist() or set())
            )

            self._record(
                "P3-V7-expansion-propagate",
                before_blocked
                and not still_blocked
                and other_blocked
                and "playwright-skill" in allow_after
                and "playwright-skill" in auth_skills
                and "playwright-skill" in facts_g
                and persisted.get("ok") is True
                and restore_ok,
                f"before_blocked={before_blocked} still_blocked={still_blocked} "
                f"other_blocked={other_blocked} allow={sorted(allow_after)} "
                f"auth={sorted(auth_skills)} restore={sorted(restored)} "
                f"retry_note=no_user_continue",
            )
        finally:
            set_telemetry_context(None)

    def test_v8_kb_qa_effective_allowlist_honors_grants(self):
        """kb_qa 区不得因默认空主类丢弃已写入的 granted_auxiliary。"""
        from orchestrator_integration.skill_zones import ZONE_KB_QA
        from orchestrator_integration.task_commission import TaskCommission

        c = TaskCommission(
            task_id="kb-grant",
            zone_id=ZONE_KB_QA,
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset({"playwright-skill"}),
            short_circuit=False,
            draft_writes=False,
            channel="light",
        )
        empty_ok = c.effective_allowlist() == set()
        c.grant_skills({"playwright-skill"})
        granted = c.effective_allowlist() or set()
        self._record(
            "P3-V8-kb-qa-grant",
            empty_ok and "playwright-skill" in granted,
            f"empty_ok={empty_ok} granted={sorted(granted)}",
        )

    def test_v9_kb_qa_zone_elevates_after_expansion(self):
        """kb_qa 硬拦 execute；获批/挂载执行后同轮重试须通过分区门。"""
        from orchestrator_integration.skill_governance import (
            elevate_zone_for_authorized_execute,
            reject_zone_privilege_escalation,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_BROWSER,
            ZONE_KB_QA,
        )

        blocked = reject_zone_privilege_escalation(
            zone_id=ZONE_KB_QA,
            skill_name="playwright-skill",
            command='node run.js "await page.goto(\'https://example.com\')"',
        )
        still_hard = blocked is not None and "知识库问答区" in blocked

        elevated = elevate_zone_for_authorized_execute(
            ZONE_KB_QA,
            authorized_skills={"playwright-skill"},
            skill_name="playwright-skill",
            mount_execute=True,
        )
        after = reject_zone_privilege_escalation(
            zone_id=ZONE_KB_QA,
            skill_name="playwright-skill",
            command='node run.js "await page.goto(\'https://example.com\')"',
            authorized_skills={"playwright-skill"},
            mount_execute=True,
        )
        self._record(
            "P3-V9-kb-qa-elevate",
            still_hard and elevated == ZONE_BROWSER and after is None,
            f"hard={still_hard} elevated={elevated} after_ok={after is None}",
        )

    def test_v10_auth_gate_reject_stops_budget_and_escalates(self):
        """扩权后重试仍被门拒 → 报平台阻塞；门拒本身不扣预算。"""
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools
        from orchestrator_integration.skill_zones import ZONE_KB_QA
        from orchestrator_integration.task_commission import TaskCommission
        from orchestrator_integration.task_orchestrator.node_runtime import (
            apply_remount_to_context,
        )
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        task = self._task(
            status="running",
            auth_scope={"allow_actions": [], "deny_actions": []},
            facts={},
            budget={
                "max_tool_actions": 76,
                "used_tool_actions": 28,
                "max_tool_steps": 76,
                "used_tool_steps": 28,
            },
        )
        commission = TaskCommission(
            task_id="p3-v10",
            zone_id=ZONE_KB_QA,
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset({"playwright-skill"}),
            short_circuit=False,
            draft_writes=False,
            channel="light",
            max_tool_steps=76,
            tool_steps_used=28,
        )
        tools = {
            t.name: t
            for t in get_skill_tools(
                user_id=1,
                project_id=1,
                allowed_skill_names=set(),
                allow_execute=True,
            )
        }
        set_telemetry_context(
            TelemetryContext(
                zone=ZONE_KB_QA,
                commission=commission,
                runtime_task_id=task.pk,
                granted_skills=set(),
                tools_mounted=["read_skill_content", "execute_skill_script"],
            )
        )
        try:
            # 扩权 remount
            apply_remount_to_context({"playwright-skill"})
            task.refresh_from_db()
            used_before = int(
                (task.budget or {}).get(
                    "used_tool_actions",
                    (task.budget or {}).get("used_tool_steps", 28),
                )
            )
            # 故意用不在授权内的 skill 触发门拒（扩权后 awaiting_retry）
            denied = tools["execute_skill_script"].invoke(
                {
                    "skill_name": "browser-use",
                    "command": "node run.js \"await page.goto('https://x')\"",
                }
            )
            task.refresh_from_db()
            used_after = int(
                (task.budget or {}).get(
                    "used_tool_actions",
                    (task.budget or {}).get("used_tool_steps", used_before),
                )
            )
            facts = task.facts or {}
            escalate_ok = "平台阻塞" in denied
            no_budget_burn = used_after == used_before
            # 获批 playwright：分区门应放行（不实际跑浏览器）
            from orchestrator_integration.skill_governance import (
                reject_zone_privilege_escalation,
            )
            from orchestrator_integration.telemetry_context import (
                get_telemetry_context,
            )

            ctx = get_telemetry_context()
            pw_priv = reject_zone_privilege_escalation(
                zone_id=ctx.zone or ZONE_KB_QA,
                skill_name="playwright-skill",
                command="node run.js \"await page.goto('https://example.com')\"",
                authorized_skills=set(ctx.granted_skills or []) | {"playwright-skill"},
                mount_execute=True,
            )
            zone_ok = (ctx.zone or "") != ZONE_KB_QA and pw_priv is None
            self._record(
                "P3-V10-gate-stop-budget",
                escalate_ok and no_budget_burn and zone_ok,
                f"escalate={escalate_ok} budget_same={no_budget_burn} "
                f"used={used_before}->{used_after} "
                f"zone={ctx.zone} pw_priv={pw_priv} "
                f"blocked={facts.get('platform_auth_blocked')}",
            )
        finally:
            set_telemetry_context(None)
