"""缺证据 + 有工具/预算 → 应自动续跑，勿甩给用户补证。"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from langgraph_integration.models import ChatSession


class EvidenceAutoContinuePolicyTests(SimpleTestCase):
    def test_zero_progress_with_budget_triggers_continue(self):
        from orchestrator_integration.final_response_policy import (
            should_attempt_checkpoint_continue,
        )

        # 模型未用「任务已完成」口吻，但业务 0/3、预算有余 → 仍应续跑
        self.assertTrue(
            should_attempt_checkpoint_continue(
                response_text="本轮先整理了检查点清单。",
                executor_mounted=True,
                user_stopped=False,
                interrupt_detected=False,
                used_tool_actions=12,
                reserved_tool_actions=0,
                max_tool_actions=20,
                completed_business_steps=0,
                expected_business_steps=3,
                missing_evidence=True,
            )
        )

    def test_no_continue_when_budget_exhausted(self):
        from orchestrator_integration.final_response_policy import (
            should_attempt_checkpoint_continue,
        )

        self.assertFalse(
            should_attempt_checkpoint_continue(
                response_text="本轮先整理了检查点清单。",
                executor_mounted=True,
                user_stopped=False,
                interrupt_detected=False,
                used_tool_actions=20,
                reserved_tool_actions=0,
                max_tool_actions=20,
                completed_business_steps=0,
                expected_business_steps=3,
                missing_evidence=True,
            )
        )

    def test_no_continue_when_executor_not_mounted(self):
        from orchestrator_integration.final_response_policy import (
            recovery_for_missing_evidence,
            should_attempt_checkpoint_continue,
        )

        self.assertFalse(
            should_attempt_checkpoint_continue(
                response_text="需要补证据。",
                executor_mounted=False,
                user_stopped=False,
                interrupt_detected=False,
                used_tool_actions=2,
                reserved_tool_actions=0,
                max_tool_actions=20,
                completed_business_steps=0,
                expected_business_steps=3,
                missing_evidence=True,
            )
        )
        rec = recovery_for_missing_evidence(
            executor_mounted=False, budget_remaining=True
        )
        self.assertIn("工具挂载阻塞", rec)
        self.assertIn("禁止要求用户", rec)
        self.assertNotIn("补齐工具/页面", rec)

    def test_recovery_with_tools_does_not_ask_user_for_evidence(self):
        from orchestrator_integration.final_response_policy import (
            correction_for_unverified_delivery,
            recovery_for_missing_evidence,
        )

        rec = recovery_for_missing_evidence(
            executor_mounted=True, budget_remaining=True
        )
        self.assertIn("自动续跑", rec)
        self.assertNotIn("发送「继续」", rec)
        corr = correction_for_unverified_delivery(
            acceptance={"reasons": ["验收条件缺少合格证据"]},
            completed_business_steps=0,
            expected_business_steps=3,
            executor_mounted=True,
            budget_remaining=True,
        )
        self.assertIn("自动续跑", corr)
        self.assertNotIn("补齐本轮页面/截图/工具证据后发送", corr)


class EvidenceAutoContinueGateTests(TestCase):
    def test_gate_recovery_prefers_server_continue(self):
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.result_evidence_delivery import (
            gate_completion_on_evidence,
        )

        User = get_user_model()
        user = User.objects.create_user(username="eac1", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id="eac-sess-1", title="t"
        )
        task = AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id="eac000001",
            goal="商品中心三检查点回归",
            status="running",
            current_stage="verify_deliver",
            stage_plan=[
                {"stage_id": "check_executable", "completion_checks": ["页面可访问"]},
                {"stage_id": "verify_deliver", "completion_checks": ["证据齐全"]},
            ],
            budget={
                "expected_business_steps": 3,
                "completed_business_steps": 0,
                "used_tool_actions": 12,
                "max_tool_actions": 20,
            },
            facts={"executor_mounted": True},
        )
        gated = gate_completion_on_evidence(task, {"ok": True, "reasons": []})
        self.assertFalse(gated.get("ok"))
        acc = gated.get("acceptance") or {}
        recovery = str(acc.get("recovery") or "")
        self.assertIn("自动续跑", recovery)
        self.assertNotIn("补齐工具/页面/DB/文件证据后发送", recovery)
        self.assertTrue(
            (acc.get("evidence_gate") or {}).get("auto_continue_eligible")
        )

    def test_close_task_missing_evidence_recovery_not_user_supply(self):
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.task_state_closure import close_task_after_stream

        User = get_user_model()
        user = User.objects.create_user(username="eac2", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id="eac-sess-2", title="t"
        )
        task = AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id="eac000002",
            goal="三检查点",
            status="running",
            current_stage="verify_deliver",
            stage_plan=[
                {"stage_id": "a", "acceptance": "x"},
                {"stage_id": "b", "acceptance": "y"},
                {"stage_id": "c", "acceptance": "z"},
            ],
            budget={
                "expected_business_steps": 3,
                "completed_business_steps": 0,
                "used_tool_actions": 12,
                "max_tool_actions": 20,
            },
            facts={"executor_mounted": True},
        )
        result = close_task_after_stream(
            task,
            acceptance={
                "ok": False,
                "reasons": ["验收条件缺少合格证据: a (missing_evidence)"],
                "blockers": [{"blocker_code": "missing_evidence"}],
                "evidence_gate": {"ok": False, "auto_continue_eligible": True},
            },
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        self.assertEqual(result.get("action"), "blocked")
        recovery = str(result.get("recovery") or "")
        self.assertIn("自动续跑", recovery)
        self.assertNotIn("补齐可追溯证据或消除矛盾后发送", recovery)
