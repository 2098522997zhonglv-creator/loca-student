"""编排硬门禁：input_refs、跨工具 ID、弱断言、L3 批准。"""

from __future__ import annotations

import unittest
from unittest import mock

from orchestrator_integration.intent_router import IntentDecision
from orchestrator_integration.task_orchestrator import build_task_plan
from orchestrator_integration.task_orchestrator.gates import (
    assert_cross_tool_object_ids,
    check_input_refs,
    reject_weak_assertions,
)
from orchestrator_integration.task_orchestrator.plan import NODE_DONE, NODE_RUNNING
from orchestrator_integration.execution_policy import evaluate_post_condition


def _dec():
    return IntentDecision(intents=set(), primary="qa", reason="t")


class InputRefsGateTests(unittest.TestCase):
    def test_run_blocked_without_prepare_objects(self):
        plan = build_task_plan(
            "执行测试用例：复杂参数化，需要数据准备与清理",
            _dec(),
        )
        for n in plan.nodes:
            if n.node_id in {"query_cases", "prepare_data"}:
                n.status = NODE_DONE
            elif n.node_id == "run_or_browser":
                n.status = NODE_RUNNING
                plan.current_node = "run_or_browser"
        run = plan.current()
        err = check_input_refs(plan, run, {"case_ref": {"case_id": 1}})
        self.assertIsNotNone(err)
        self.assertIn("硬门禁", err)

    def test_run_ok_with_prepared_object(self):
        plan = build_task_plan(
            "执行测试用例：复杂参数化，需要数据准备与清理",
            _dec(),
        )
        for n in plan.nodes:
            if n.node_id in {"query_cases", "prepare_data"}:
                n.status = NODE_DONE
            elif n.node_id == "run_or_browser":
                n.status = NODE_RUNNING
                plan.current_node = "run_or_browser"
        run = plan.current()
        err = check_input_refs(
            plan,
            run,
            {
                "case_ref": {"case_id": 1},
                "prepared_object_ids": ["obj-9"],
                "object_id": "obj-9",
            },
        )
        self.assertIsNone(err)

    def test_simple_run_only_needs_case_ref(self):
        plan = build_task_plan("执行测试用例 42", _dec())
        plan.current_node = "run_or_browser"
        for n in plan.nodes:
            if n.node_id == "query_cases":
                n.status = NODE_DONE
            elif n.node_id == "run_or_browser":
                n.status = NODE_RUNNING
        err = check_input_refs(
            plan, plan.current(), {"case_ref": {"case_id": 42}}
        )
        self.assertIsNone(err)


class CrossToolIdTests(unittest.TestCase):
    def test_mismatch_detected(self):
        err = assert_cross_tool_object_ids(
            {"object_id": "A-1"},
            '{"object_id":"B-2","status":"ok"}',
        )
        self.assertIsNotNone(err)
        self.assertIn("不一致", err)

    def test_same_id_ok(self):
        err = assert_cross_tool_object_ids(
            {"object_id": "A-1", "record_id": 3},
            '{"object_id":"A-1","record_id":3}',
        )
        self.assertIsNone(err)


class WeakAssertionTests(unittest.TestCase):
    def test_reject_url_order_claim(self):
        reasons = reject_weak_assertions(
            ["页面 url=https://x/order/1 测试通过"],
        )
        self.assertTrue(any("URL" in r or "弱断言" in r for r in reasons))

    def test_reject_outer_success_as_pass(self):
        reasons = reject_weak_assertions(
            ['{"status":"success"} 用例通过'],
        )
        self.assertTrue(reasons)

    def test_real_passed_not_rejected(self):
        reasons = reject_weak_assertions(
            ['{"test_result":"passed","status":"success"} 用例通过'],
            facts={"test_result": "passed"},
        )
        # 有真实 passed 时不应再因 outer success 单独拒绝 claim
        self.assertFalse(
            any("外层 success" in r for r in reasons),
            msg=reasons,
        )

    def test_evaluate_post_condition_includes_weak(self):
        v = evaluate_post_condition(
            post_condition="完成",
            tool_results=['工具 succeeded，URL 含 order，验收通过'],
            playbook_id="testcase_execute",
            facts={},
        )
        self.assertFalse(v.get("ok"))
        self.assertTrue(any("弱断言" in r for r in (v.get("reasons") or [])))


class L3ApprovalApplyTests(unittest.TestCase):
    def test_apply_l3_marks_pending(self):
        import sys
        import types

        from orchestrator_integration.task_orchestrator.flag import (
            merge_strategy_into_facts,
        )
        from orchestrator_integration.task_orchestrator.gates import (
            apply_l3_approval_to_task,
        )
        from orchestrator_integration.task_orchestrator.plan import PendingAuthAction

        plan = build_task_plan(
            "上传测试图，提交测试订单，给我订单号，不支付",
            IntentDecision(
                intents={"browser", "search"}, primary="browser", reason="t"
            ),
        )
        # ensure pending exists
        if not plan.pending_auth:
            plan.pending_auth.append(
                PendingAuthAction(
                    action_id="submit_order:submit_order",
                    task_id=plan.task_id,
                    node_id="submit_order",
                    target="submit_order",
                )
            )
        facts = merge_strategy_into_facts({}, enabled=True)
        facts["task_plan"] = plan.to_dict()
        task = mock.MagicMock()
        task.pk = 9
        task.facts = facts
        task.auth_scope = {}
        task.current_stage = plan.current_node

        models_mod = types.ModuleType("orchestrator_integration.models")
        models_mod.AgentTask = mock.MagicMock()
        models_mod.AgentTask.objects.filter.return_value.update.return_value = 1
        tz = types.ModuleType("django.utils.timezone")
        tz.now = mock.MagicMock()
        with mock.patch.dict(
            sys.modules,
            {
                "orchestrator_integration.models": models_mod,
                "django.utils.timezone": tz,
            },
        ):
            out = apply_l3_approval_to_task(
                task, approve=True, targets=["submit_order"]
            )
        self.assertTrue(out.get("ok"))
        plan2 = task.facts.get("task_plan") or {}
        pending = plan2.get("pending_auth") or []
        self.assertTrue(
            any(p.get("approval") == "approved" for p in pending),
            msg=pending,
        )


if __name__ == "__main__":
    unittest.main()
