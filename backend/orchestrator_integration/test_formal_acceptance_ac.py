"""V1.1 正式验收 AC01–AC30 自动化回归（隔离/确定性断言）。

不访问真实 Callie；浏览器与支付 E2E 仍需专用环境人工补测。
运行：
  python manage.py test orchestrator_integration.test_formal_acceptance_ac -v2
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from orchestrator_integration.formal_acceptance_scenarios import (
    AC_SCENARIOS,
    assert_matrix_complete,
)


class FormalAcceptanceMatrixTests(SimpleTestCase):
    def test_matrix_has_ac01_to_ac30(self):
        assert_matrix_complete()
        self.assertEqual(len(AC_SCENARIOS), 30)


class FormalAcceptanceAC01to07Tests(SimpleTestCase):
    def test_ac01_nonzero_exit_failed(self):
        from orchestrator_integration.tool_result import (
            classify_skill_output,
            summarize_batch_statuses,
        )

        failed = classify_skill_output(
            text="命令执行失败 (退出码: 1)\nboom",
            exit_code=1,
            skill_name="playwright-skill",
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "nonzero_exit")
        summary = summarize_batch_statuses([failed])
        self.assertFalse(summary["ok"])

    def test_ac02_business_rejection_failed_no_blind_retry(self):
        from orchestrator_integration.retry_policy import decide_retry
        from orchestrator_integration.tool_result import classify_skill_output

        tr = classify_skill_output(
            text="余额不足，业务校验失败",
            skill_name="loca-stude",
        )
        self.assertEqual(tr.status, "failed")
        self.assertEqual(tr.error_code, "business_rejected")
        decision = decide_retry(
            skill_name="loca-stude",
            attempt=0,
            channel="light",
            error_text="余额不足，业务校验失败",
        )
        self.assertFalse(decision.allow)

    def test_ac03_batch_preserves_create_then_query_order(self):
        from orchestrator_integration.execution_policy import plan_batch_execution

        err, plan = plan_batch_execution(
            [
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action add_testcase --name a",
                },
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action get_testcases --id ${a.id}",
                },
            ],
            want_parallel=False,
            channel="heavy",
        )
        self.assertIsNone(err)
        self.assertIn("add_testcase", plan.ordered_commands[0]["command"])
        self.assertIn("get_testcases", plan.ordered_commands[1]["command"])

    def test_ac04_dependency_failed_skips_dependents(self):
        from orchestrator_integration.execution_policy import resolve_dependent_skips
        from orchestrator_integration.step_deps import apply_dependency_skips

        planned = resolve_dependent_skips(
            [
                {
                    "step_id": "a",
                    "status": "failed",
                    "depends_on": [],
                    "on_failure": "stop_dependents",
                },
                {
                    "step_id": "b",
                    "status": "pending",
                    "depends_on": ["a"],
                    "on_failure": "stop_dependents",
                },
                {
                    "step_id": "c",
                    "status": "pending",
                    "depends_on": [],
                    "on_failure": "continue",
                },
            ]
        )
        by_id = {s["step_id"]: s for s in planned}
        self.assertEqual(by_id["b"]["status"], "skipped")
        self.assertEqual(by_id["c"]["status"], "pending")
        applied = apply_dependency_skips(
            [
                {"step_id": "a", "status": "failed", "depends_on": []},
                {"step_id": "b", "depends_on": ["a"]},
            ],
            failed_step_ids={"a"},
        )
        self.assertEqual(applied[1]["status"], "skipped")

    def test_ac05_browser_page_actions_forced_serial(self):
        from orchestrator_integration.execution_policy import plan_batch_execution

        err, plan = plan_batch_execution(
            [
                {
                    "skill_name": "playwright-skill",
                    "command": "node run.js \"await page.click('#a')\"",
                },
                {
                    "skill_name": "playwright-skill",
                    "command": "node run.js \"await page.click('#b')\"",
                },
            ],
            want_parallel=True,
            channel="light",
        )
        self.assertIsNone(err)
        # 含页面副作用时强制串行
        self.assertFalse(plan.parallel)

    def test_ac06_browser_timeout_only_cannot_complete(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="done",
            tool_results=['page.waitForEvent: Timeout 15000ms exceeded while waiting for event "filechooser"'],
            playbook_id="browser_act",
        )
        self.assertFalse(acc["ok"])

    def test_ac07_execution_terminal_failed_can_complete_task(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        # 有执行记录终态=failed：任务完成，业务结果为失败
        acc = evaluate_post_condition(
            post_condition="exec",
            tool_results=["execution_id=e1 终态=failed 断言不通过"],
            playbook_id="testcase_execute",
        )
        self.assertTrue(acc["ok"])


class FormalAcceptanceAC08to15DBTests(TestCase):
    def _task(self, *, user, session_id: str, goal: str = "x", max_steps: int = 20):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import create_or_resume_task
        from orchestrator_integration.task_commission import TaskCommission

        chat = ChatSession.objects.create(user=user, session_id=session_id, title="t")
        c = TaskCommission(
            task_id=session_id[-8:],
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
            max_tool_steps=max_steps,
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal=goal,
            commission=c,
            auth_scope={"allow_actions": ["add_to_cart", "upload"], "deny_actions": ["pay", "submit_order"]},
            lease_owner="owner-a",
        )
        return chat, task, c

    def test_ac08_lease_contention(self):
        from orchestrator_integration.agent_runtime import try_claim_execution

        User = get_user_model()
        user = User.objects.create_user(username="ac08", password="x")
        _, task, _ = self._task(user=user, session_id="sess-ac08")
        claim2 = try_claim_execution(task, "owner-b")
        self.assertFalse(claim2["acquired"])
        self.assertEqual(claim2["error_code"], "lease_held")

    def test_ac09_uncertain_must_reconcile_before_replay(self):
        from orchestrator_integration.agent_runtime import resume_preflight
        from orchestrator_integration.models import AgentStep

        User = get_user_model()
        user = User.objects.create_user(username="ac09", password="x")
        _, task, _ = self._task(user=user, session_id="sess-ac09")
        AgentStep.objects.create(
            task=task,
            step_number=1,
            step_id="op1",
            status="unknown",
            tool_name="ui-automation",
            tool_output_summary="timeout",
            structured_result={"status": "unknown", "data": {"execution_id": "e9"}},
        )
        pre = resume_preflight(task)
        self.assertTrue(pre["must_reconcile"])
        self.assertTrue(pre["uncertain_steps"])

    def test_ac10_cross_user_cannot_consume_approval(self):
        from orchestrator_integration.models import ApprovalRequest
        from orchestrator_integration.write_confirmation import consume_approval

        User = get_user_model()
        user = User.objects.create_user(username="ac10a", password="x")
        other = User.objects.create_user(username="ac10b", password="x")
        chat, task, _ = self._task(user=user, session_id="sess-ac10")
        ApprovalRequest.objects.create(
            interrupt_id="write_confirm_ac10",
            user=user,
            session_id=chat.session_id,
            task=task,
            action_name="execute_skill_script",
            action_args={"pending": {"command": "x"}},
            param_fingerprint="fp10",
            status="pending",
        )
        denied = consume_approval(
            "write_confirm_ac10", user_id=other.id, decision="approve"
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error_code"], "permission_denied")

    def test_ac11_repeat_approve_and_fingerprint_mismatch(self):
        from orchestrator_integration.agent_runtime import fingerprint_payload
        from orchestrator_integration.models import ApprovalRequest
        from orchestrator_integration.write_confirmation import consume_approval

        User = get_user_model()
        user = User.objects.create_user(username="ac11", password="x")
        chat, task, _ = self._task(user=user, session_id="sess-ac11")
        payload = {"pending": {"command": "add"}}
        fp = fingerprint_payload(payload)
        ApprovalRequest.objects.create(
            interrupt_id="write_confirm_ac11",
            user=user,
            session_id=chat.session_id,
            task=task,
            action_name="execute_skill_script",
            action_args=payload,
            param_fingerprint=fp,
            status="pending",
        )
        ok = consume_approval(
            "write_confirm_ac11",
            user_id=user.id,
            decision="approve",
            expected_fingerprint=fp,
            session_id=chat.session_id,
        )
        self.assertTrue(ok["ok"])
        again = consume_approval(
            "write_confirm_ac11", user_id=user.id, decision="approve"
        )
        self.assertFalse(again["ok"])
        ApprovalRequest.objects.create(
            interrupt_id="write_confirm_ac11b",
            user=user,
            session_id=chat.session_id,
            task=task,
            action_name="execute_skill_script",
            action_args=payload,
            param_fingerprint=fp,
            status="pending",
        )
        mismatch = consume_approval(
            "write_confirm_ac11b",
            user_id=user.id,
            decision="approve",
            expected_fingerprint="wrong",
            session_id=chat.session_id,
        )
        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["error_code"], "param_fingerprint_mismatch")

    def test_ac14_budget_persists_across_resume_like_reload(self):
        from orchestrator_integration.agent_runtime import consume_budget
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        user = User.objects.create_user(username="ac14", password="x")
        _, task, _ = self._task(user=user, session_id="sess-ac14", max_steps=12)
        ok, _ = consume_budget(task, tool_steps=3, retries=1)
        self.assertTrue(ok)
        used = task.budget["used_tool_steps"]
        retries = task.budget["used_retries"]
        reloaded = AgentTask.objects.get(pk=task.pk)
        self.assertEqual(reloaded.budget["used_tool_steps"], used)
        self.assertEqual(reloaded.budget["used_retries"], retries)
        # 恢复不重置
        ok2, _ = consume_budget(reloaded, tool_steps=1)
        self.assertTrue(ok2)
        self.assertEqual(reloaded.budget["used_tool_steps"], used + 1)

    def test_ac15_stop_sets_cancelling_and_blocks_finalize_with_unknown(self):
        from orchestrator_integration.agent_runtime import (
            finalize_cancel,
            request_cancel,
        )
        from orchestrator_integration.models import AgentStep
        from orchestrator_integration.stop_signal import (
            clear_stop_signal,
            should_stop,
        )

        User = get_user_model()
        user = User.objects.create_user(username="ac15", password="x")
        chat, task, _ = self._task(user=user, session_id="sess-ac15")
        AgentStep.objects.create(
            task=task,
            step_number=1,
            step_id="u1",
            status="unknown",
            tool_name="ui-automation",
            structured_result={"status": "unknown", "data": {"execution_id": "ex"}},
        )
        cancel = request_cancel(task)
        self.assertEqual(cancel["status"], "cancelling")
        self.assertTrue(should_stop(chat.session_id))
        fin = finalize_cancel(task)
        self.assertEqual(fin["status"], "cancelling")
        clear_stop_signal(chat.session_id)


class FormalAcceptanceAC12AC13UnitTests(SimpleTestCase):
    def test_ac12_auth_scope_allows_continuous_authorized_actions(self):
        from orchestrator_integration.stage_plan import infer_auth_scope

        auth = infer_auth_scope("详情页传图后加入购物车，不要支付")
        self.assertIn("upload", auth["allow_actions"])
        self.assertIn("add_to_cart", auth["allow_actions"])
        self.assertIn("pay", auth["deny_actions"])
        self.assertIn("submit_order", auth["deny_actions"])

    def test_ac13_timeout_reconcile_not_replay(self):
        from orchestrator_integration.reconcile import build_reconcile_plan

        plan = build_reconcile_plan(
            skill_name="ui-automation",
            text="错误: 命令执行超时（120秒） execution_id=e13",
            status="unknown",
        )
        self.assertTrue(plan["must_reconcile"])
        self.assertFalse(plan["allow_replay"])


class FormalAcceptanceAC16to30Tests(TestCase):
    def test_ac16_composite_stage_plan_not_collapsed(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import build_stage_plan, stage_mount_policy

        text = "根据知识库登录规则生成用例并保存后执行，再汇总"
        d = classify_user_intent(text)
        stages = build_stage_plan(text, d, playbook_id="testcase_execute")
        ids = [s.stage_id for s in stages]
        self.assertGreater(len(ids), 1)
        self.assertIn("kb_research", ids)
        policy = stage_mount_policy(stages, "kb_research")
        self.assertIn("knowledge_search", policy.get("builtin_tools") or [])

    def test_ac17_continue_preserves_goal_and_auth(self):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import create_or_resume_task
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="ac17", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-ac17", title="t")
        c = TaskCommission(
            task_id="ac17",
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="加购不要下单",
            commission=c,
            auth_scope={
                "allow_actions": ["add_to_cart"],
                "deny_actions": ["submit_order"],
            },
            lease_owner="w1",
        )
        cont = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="继续完成剩下的",
            commission=c,
            auth_scope={"allow_actions": ["submit_order"]},
            lease_owner="w1",
        )
        self.assertEqual(cont.goal, task.goal)
        self.assertNotIn("submit_order", cont.auth_scope.get("allow_actions") or [])
        self.assertIn("submit_order", cont.auth_scope.get("deny_actions") or [])

    def test_ac18_snapshot_event_replay_filter(self):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            append_event,
            create_or_resume_task,
            task_snapshot,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="ac18", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-ac18", title="t")
        c = TaskCommission(
            task_id="ac18",
            zone_id="general",
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="light",
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="x",
            commission=c,
            lease_owner="w1",
        )
        e1 = append_event(task, "step_result", status="running", payload={"n": 1})
        e2 = append_event(task, "step_result", status="running", payload={"n": 2})
        snap = task_snapshot(task)
        events = snap.get("events") or []
        self.assertGreaterEqual(len(events), 2)
        after = [e for e in events if (e.get("sequence") or 0) > e1["sequence"]]
        self.assertTrue(any(e.get("event_id") == e2["event_id"] for e in after))
        self.assertFalse(any(e.get("event_id") == e1["event_id"] for e in after))

    def test_ac19_natural_language_browser_intent(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import infer_auth_scope
        from orchestrator_integration.turn_context import (
            PriorTaskContext,
            classify_turn_relation,
            should_preserve_execution,
        )

        for text in (
            "帮我上传后加购",
            "继续完成加购",
            "把图传上去并加入购物车",
        ):
            d = classify_user_intent(text)
            auth = infer_auth_scope(text)
            self.assertTrue(
                "browser" in (d.intents or set())
                or "add_to_cart" in auth["allow_actions"]
                or "upload" in auth["allow_actions"],
                msg=text,
            )
        # 多轮：无「继续」词的环境补充仍复用未完成浏览器任务
        prior = PriorTaskContext(
            task_pk=1,
            public_task_id="ac19",
            goal="上传后加购 https://a.callie.cc/p1",
            current_stage="browser_interact",
            auth_scope={"allow_actions": ["upload", "add_to_cart"]},
        )
        follow = "后台用测试环境，站点选美站。"
        rel = classify_turn_relation(follow, prior)
        self.assertTrue(should_preserve_execution(prior, rel), rel)

    def test_ac20_route_correction_to_browser(self):
        from orchestrator_integration.intent_router import IntentDecision
        from orchestrator_integration.skill_zones import (
            ZONE_BROWSER,
            ZONE_KB_QA,
            SkillZone,
            correct_misrouted_zone,
            zone_skills,
        )
        from orchestrator_integration.turn_context import TURN_SUPPLEMENT

        text = "打开这个商品页上传图片后加购 https://a.callie.cc/p1"
        bad = SkillZone(ZONE_KB_QA, frozenset(), allow_execute=False, reason="kb_qa")
        fixed = correct_misrouted_zone(
            text, bad, IntentDecision(intents=set(), primary="qa")
        )
        self.assertEqual(fixed.zone_id, ZONE_BROWSER)
        self.assertEqual(fixed.allowed_skills, zone_skills(ZONE_BROWSER))
        # 多轮：仅补参、无操作关键词，也须据 prior 升区
        patched = correct_misrouted_zone(
            "站点选美站",
            bad,
            IntentDecision(intents=set(), primary="qa"),
            prior_stage="browser_interact",
            prior_auth={"allow_actions": ["upload", "add_to_cart"]},
            turn_relation=TURN_SUPPLEMENT,
        )
        self.assertEqual(patched.zone_id, ZONE_BROWSER)
        self.assertIn("prior_task", patched.reason)

    def test_ac21_order_branch_clarification_does_not_block_cart_auth(self):
        from orchestrator_integration.stage_plan import infer_auth_scope

        auth = infer_auth_scope("上传并加购，订单号如需再问我，不要支付")
        self.assertIn("upload", auth["allow_actions"])
        self.assertIn("add_to_cart", auth["allow_actions"])
        self.assertIn("submit_order", auth["deny_actions"])

    def test_ac22_add_to_cart_without_submit_order(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence
        from orchestrator_integration.stage_plan import infer_auth_scope

        auth = infer_auth_scope("只加购，不要支付，不要下单")
        self.assertNotIn("submit_order", auth["allow_actions"])
        self.assertIn("submit_order", auth["deny_actions"])
        delivery = evaluate_delivery_evidence(
            tool_results=["提交订单成功 order_sn=123"],
            auth_scope=auth,
        )
        self.assertFalse(delivery["ok"])

    def test_ac23_probe_missing_resources(self):
        from orchestrator_integration.agent_runtime import (
            format_probe_hint,
            probe_browser_resources,
        )

        missing = probe_browser_resources(file_path="/tmp/no_such_upload_xyz.png")
        self.assertFalse(missing["probe_result"]["file"]["accessible"])
        hint = format_probe_hint(missing)
        self.assertIn("禁止宣称上传成功", hint)
        sess = probe_browser_resources(session_id="sess-not-exist-xyz")
        self.assertFalse(sess["ok_to_reuse"])

    def test_ac24_native_requirement_rejects_set_input(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="upload",
            tool_results=["used setInputFiles; 0/1 → 1/1"],
            playbook_id="browser_act",
            auth_scope={"upload_method_requirement": "native_or_filechooser"},
        )
        self.assertFalse(acc["ok"])

    def test_ac25_count_only_without_preview_fails(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="upload",
            tool_results=["计数 0/1 → 1/1；preview not refreshed"],
            playbook_id="browser_act",
        )
        self.assertFalse(acc["ok"])

    def test_ac26_cart_with_typed_id_passes_browser_checks(self):
        from orchestrator_integration.execution_policy import evaluate_browser_checkpoints

        chk = evaluate_browser_checkpoints(
            "Add To Cart ok; preview refreshed; cart_id=cart_abc123; 素材已关联"
        )
        self.assertTrue(chk["ok"])
        self.assertTrue(chk["checks"]["has_typed_identifier"])

    def test_ac27_domain_inference_blocked(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        delivery = evaluate_delivery_evidence(
            tool_results=["文档写明仅有143和dnd展示支持，当前分支不同因此不支持"],
        )
        self.assertFalse(delivery["ok"])

    def test_ac28_kb_sample_order_sn_rejected(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        delivery = evaluate_delivery_evidence(
            tool_results=["示例订单号 12345678901234567；交付 order_sn=12345678901234567"],
        )
        self.assertFalse(delivery["ok"])

    def test_ac29_missing_identifier_not_complete(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="cart",
            tool_results=["Add To Cart ok; preview refreshed; 已加入购物车"],
            playbook_id="browser_act",
            auth_scope={"allow_actions": ["add_to_cart"]},
        )
        self.assertFalse(acc["ok"])
        self.assertTrue(any("类型化标识" in r for r in acc["reasons"]))

    def test_ac30_other_product_cannot_replace_target(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        delivery = evaluate_delivery_evidence(
            tool_results=["另一商品属性区为空，所见所得标题类似"],
            target_url="https://a.callie.cc/p1",
        )
        self.assertFalse(delivery["ok"])

    def test_ac31_historical_502_not_current_fault(self):
        from orchestrator_integration.evidence_integrity import (
            classify_http_failure_freshness,
            evaluate_evidence_integrity_text,
        )
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        self.assertEqual(
            classify_http_failure_freshness("上一轮请求返回 HTTP 502"),
            "historical_unverified",
        )
        bad = evaluate_evidence_integrity_text(
            "上一轮 502，请恢复环境后再试",
            browser_actions_this_turn=0,
        )
        self.assertFalse(bad["ok"])
        delivery = evaluate_delivery_evidence(
            tool_results=["上一轮 502，请恢复网关服务后再执行加购"],
        )
        self.assertFalse(delivery["ok"])

    def test_ac32_probe_required_before_claiming_resources(self):
        from orchestrator_integration.agent_runtime import format_probe_hint
        from orchestrator_integration.evidence_integrity import (
            evaluate_evidence_integrity_text,
        )

        claim = evaluate_evidence_integrity_text(
            "cookie 仍在，完整执行输入已具备，可直接加购",
            probe_ok_to_reuse=None,
        )
        self.assertFalse(claim["ok"])
        hint = format_probe_hint(
            {
                "observed_at": "2026-01-01T00:00:00",
                "ok_to_reuse": False,
                "probe_result": {
                    "session": {
                        "claimed_id": "s1",
                        "alive": False,
                        "note": "session_not_in_process_memory",
                        "login_state": "unavailable",
                        "cookie_count": 3,
                    }
                },
            }
        )
        self.assertIn("历史异常，尚未复核", hint)
        self.assertIn("cookie_count", hint)
        self.assertIn("不等于当前登录有效", hint)

    def test_ac33_progress_label_without_actions(self):
        from orchestrator_integration.evidence_integrity import (
            evaluate_progress_labeling,
        )

        bad = evaluate_progress_labeling(
            "已完成事项：整理了 URL 与 cookie",
            browser_actions_this_turn=0,
        )
        self.assertFalse(bad["ok"])
        good = evaluate_progress_labeling(
            "已整理的输入：URL/cookie 路径；本轮业务步骤完成数为 0",
            browser_actions_this_turn=0,
        )
        self.assertTrue(good["ok"])

    def test_ac34_delivery_no_placeholder_or_overpromise(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        placeholder = evaluate_delivery_evidence(
            tool_results=["交付 cart_id=待补充 quantity=N/A 金额=占位"],
        )
        self.assertFalse(placeholder["ok"])
        over = evaluate_delivery_evidence(
            tool_results=["将返回所有购物车标识、数量与金额"],
        )
        self.assertFalse(over["ok"])

    def test_ac35_equiv_entry_needs_original_target(self):
        from orchestrator_integration.execution_policy import evaluate_delivery_evidence

        delivery = evaluate_delivery_evidence(
            tool_results=["工具不可用，请给一个可用的等价入口"],
        )
        self.assertFalse(delivery["ok"])
