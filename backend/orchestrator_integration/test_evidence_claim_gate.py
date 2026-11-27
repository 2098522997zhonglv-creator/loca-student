"""证据声明门禁：禁止无本轮引用的 verified，回归任务不得落成 single。"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from langgraph_integration.models import ChatSession


class EvidenceClaimGateTests(SimpleTestCase):
    def test_verified_without_ref_is_demoted(self):
        from orchestrator_integration.result_evidence_delivery import (
            CLAIM_EXECUTED,
            CLAIM_VERIFIED,
            classify_claim_level,
        )

        level = classify_claim_level(
            tool_name="assert_task_postcondition",
            step_status="succeeded",
            structured={"ok": True, "claim_level": CLAIM_VERIFIED},
            evidence={},
        )
        self.assertEqual(level, CLAIM_EXECUTED)

    def test_verified_with_ref_stays_verified(self):
        from orchestrator_integration.result_evidence_delivery import (
            CLAIM_VERIFIED,
            classify_claim_level,
        )

        level = classify_claim_level(
            tool_name="assert_task_postcondition",
            step_status="succeeded",
            structured={"ok": True, "output_ref": "ev://abc", "claim_level": "verified"},
            evidence={"output_ref": "ev://abc", "evidence_id": 1},
        )
        self.assertEqual(level, CLAIM_VERIFIED)

    def test_assert_ok_false_never_verified(self):
        from orchestrator_integration.result_evidence_delivery import (
            CLAIM_EXECUTED,
            classify_claim_level,
        )

        level = classify_claim_level(
            tool_name="assert_task_postcondition",
            step_status="succeeded",
            structured={"ok": False, "reasons": ["缺证据"]},
            evidence={"output_ref": "ev://x", "evidence_id": 9},
        )
        self.assertEqual(level, CLAIM_EXECUTED)

    def test_unverified_delivery_claims_are_detected(self):
        from orchestrator_integration.final_response_policy import (
            claims_unverified_business_completion,
            correction_for_unverified_delivery,
            should_correct_unverified_delivery,
        )

        text = (
            "商品检查已完成。证据见 "
            "skill_runtime/screenshots/1/reg_std_1112_08bba729efce4d478091c31bfb18139f/"
        )
        self.assertTrue(claims_unverified_business_completion(text))
        self.assertTrue(
            should_correct_unverified_delivery(
                response_text=text,
                acceptance={
                    "ok": False,
                    "reasons": ["声明已验证但缺少可追溯证据引用"],
                    "blockers": [{"blocker_code": "verified_without_evidence"}],
                    "evidence_gate": {"ok": False},
                },
                completed_business_steps=0,
                expected_business_steps=0,
            )
        )
        corr = correction_for_unverified_delivery(
            acceptance={"reasons": ["声明已验证但缺少可追溯证据引用"]},
            completed_business_steps=0,
            expected_business_steps=0,
        )
        self.assertIn("不能据此声称", corr)
        self.assertIn("历史截图", corr)

    def test_historical_screenshot_claim_is_hard_fail(self):
        from orchestrator_integration.evidence_integrity import (
            evaluate_evidence_integrity_text,
        )

        r = evaluate_evidence_integrity_text(
            "已验证全部商品检查点；截图在 reg_std_1112_08bba729efce4d478091c31bfb18139f",
            browser_actions_this_turn=0,
        )
        self.assertFalse(r["hard_ok"])
        self.assertTrue(any("历史截图" in x for x in r["hard_reasons"]))

    def test_product_center_checklist_is_not_single_plan(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.task_execution_config import (
            expected_business_steps_from_plan,
        )
        from orchestrator_integration.task_orchestrator.plan import build_task_plan
        from orchestrator_integration.task_orchestrator.runtime_bridge import (
            sync_plan_to_stage_specs,
        )

        text = (
            "四、恢复执行后的待办（已就绪，无需你再精炼）\n"
            "商品中心常规品功能回归：打开 #/goods-list，搜索商品ID 264247-264253，"
            "按检查点依次：编辑展示→定制设置→定制价格→预览→截图。"
        )
        d = classify_user_intent(text)
        plan = build_task_plan(text, d, task_id="reg-claim-1")
        ids = [n.node_id for n in plan.nodes]
        self.assertNotEqual(ids, ["single"])
        self.assertNotIn("single", ids)
        stages = [s.to_dict() for s in sync_plan_to_stage_specs(plan)]
        expected = expected_business_steps_from_plan(stages)
        self.assertGreater(expected, 0)


class EvidenceClaimGateDbTests(TestCase):
    def test_assert_without_ref_blocks_completion(self):
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.result_evidence_delivery import (
            detect_evidence_contradictions,
            gate_completion_on_evidence,
        )

        User = get_user_model()
        user = User.objects.create_user(username="evc1", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id="evc-sess-1", title="t"
        )
        task = AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id="evc000001",
            goal="商品中心回归检查",
            status="running",
            current_stage="verify_deliver",
            stage_plan=[
                {
                    "stage_id": "verify_deliver",
                    "completion_checks": ["目标对象、状态、约定字段与证据齐全"],
                }
            ],
            budget={
                "expected_business_steps": 1,
                "completed_business_steps": 0,
                "used_tool_actions": 12,
                "max_tool_actions": 20,
            },
            facts={},
        )
        # 模拟 assert 声称 ok 但无引用
        from orchestrator_integration.models import AgentStep

        AgentStep.objects.create(
            task=task,
            step_number=1,
            step_id="s1",
            stage_id="verify_deliver",
            status="succeeded",
            tool_name="assert_task_postcondition",
            tool_output_summary='{"ok": true}',
            structured_result={"ok": True, "claim_level": "verified"},
            evidence={"claim_level": "verified"},
        )
        steps = [
            {
                "step_id": "s1",
                "stage_id": "verify_deliver",
                "status": "succeeded",
                "tool_name": "assert_task_postcondition",
                "claim_level": "executed",  # 分类已降级
                "output_ref": "",
                "evidence_id": None,
                "structured_result": {"ok": True, "claim_level": "verified"},
                "evidence": {"claim_level": "verified"},
            }
        ]
        issues = detect_evidence_contradictions(steps)
        gated = gate_completion_on_evidence(task, {"ok": True, "reasons": []})
        self.assertTrue(
            any(i.get("code") == "verified_without_evidence" for i in issues)
        )
        self.assertFalse(gated.get("ok"))
        reasons = (gated.get("acceptance") or {}).get("reasons") or []
        self.assertTrue(any("可追溯证据" in str(r) for r in reasons))
