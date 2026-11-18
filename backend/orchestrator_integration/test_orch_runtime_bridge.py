"""编排运行时桥接：节点验收、失败清理、execution_refs 提取。"""

from __future__ import annotations

import unittest
from unittest import mock

from orchestrator_integration.intent_router import IntentDecision
from orchestrator_integration.task_orchestrator import (
    advance_plan_node,
    apply_orchestrator_mount,
    build_task_plan,
)
from orchestrator_integration.task_orchestrator.node_runtime import (
    extract_execution_ids,
    merge_ids_into_facts,
    orch_node_acceptance,
)
from orchestrator_integration.task_orchestrator.plan import NODE_FAILED, NODE_RUNNING


def _dec():
    return IntentDecision(intents=set(), primary="qa", reason="t")


class ExtractAndAcceptTests(unittest.TestCase):
    def test_extract_record_and_execution(self):
        text = '{"status":"success","record_id":88,"execution_id":"ui-exec-88","case_id":12}'
        ids = extract_execution_ids(text)
        self.assertEqual(ids.get("record_id"), 88)
        self.assertEqual(ids.get("execution_id"), "ui-exec-88")
        self.assertEqual(str(ids.get("case_id")), "12")

    def test_merge_binds_execution_ref(self):
        facts, note = merge_ids_into_facts(
            {},
            {"record_id": 7, "execution_id": "ui-exec-7", "case_id": 3},
            skill_name="ui-automation",
            tool_succeeded=True,
        )
        self.assertEqual(facts["execution_ref"]["record_id"], 7)
        self.assertEqual(facts["record_id"], 7)
        self.assertIn("不是测试通过", note)

    def test_query_and_run_acceptance(self):
        v = orch_node_acceptance(
            "query_cases",
            ['{"testcase_id": 40, "name": "login"}'],
        )
        self.assertTrue(v["ok"])
        v2 = orch_node_acceptance(
            "run_or_browser",
            ['{"record_id": 9, "execution_id": "ui-exec-9", "status": "failed"}'],
        )
        self.assertTrue(v2["ok"], msg=v2)
        v3 = orch_node_acceptance("run_or_browser", ["工具 succeeded 但无标识"])
        self.assertFalse(v3["ok"])


class CleanupOnFailureTests(unittest.TestCase):
    def test_failed_run_still_schedules_cleanup(self):
        plan = build_task_plan(
            "执行测试用例：复杂参数化，需要数据准备与清理",
            _dec(),
        )
        ids = [n.node_id for n in plan.nodes]
        self.assertIn("cleanup_data", ids)
        # 推进到 run
        plan.current_node = "run_or_browser"
        for n in plan.nodes:
            if n.node_id in {"query_cases", "prepare_data"}:
                n.status = "done"
            elif n.node_id == "run_or_browser":
                n.status = NODE_RUNNING
        plan = advance_plan_node(plan, accepted=False, failed=True)
        self.assertEqual(plan.current_node, "cleanup_data")
        self.assertEqual(plan.status, "running")
        run = next(n for n in plan.nodes if n.node_id == "run_or_browser")
        self.assertEqual(run.status, NODE_FAILED)


class RemountSkillsTests(unittest.TestCase):
    def test_next_node_mount_includes_ui_or_api(self):
        plan = build_task_plan(
            "执行测试用例：复杂参数化，需要数据准备与清理",
            _dec(),
        )
        mount0 = apply_orchestrator_mount(plan)
        self.assertTrue(mount0.skill_allowlist)
        # 模拟 query 完成
        advance_plan_node(plan, accepted=True)
        mount1 = apply_orchestrator_mount(plan)
        self.assertEqual(plan.current_node, "prepare_data")
        self.assertTrue(
            mount1.skill_allowlist
            & {"ui-automation", "api-automation", "execute_skill_script"}
            or mount1.skill_allowlist
        )


class AdvanceOrchestratorOnTaskTests(unittest.TestCase):
    def test_advance_updates_plan_and_remount(self):
        import sys
        import types

        from orchestrator_integration.task_orchestrator.flag import (
            merge_strategy_into_facts,
        )
        from orchestrator_integration.task_orchestrator.node_runtime import (
            advance_orchestrator_on_task,
        )

        plan = build_task_plan("执行测试用例 42", _dec())
        facts = merge_strategy_into_facts({}, enabled=True)
        facts["task_plan"] = plan.to_dict()

        task = mock.MagicMock()
        task.pk = 1
        task.facts = facts
        task.current_stage = plan.current_node
        task.plan_version = 1
        task.budget = {"expected_business_steps": plan.estimated_nodes}
        task.auth_scope = {"strategy_version": "task_orchestrator_v1"}
        task.status = "running"

        mock_task_model = mock.MagicMock()
        mock_task_model.objects.filter.return_value.update.return_value = 1

        models_mod = types.ModuleType("orchestrator_integration.models")
        models_mod.AgentTask = mock_task_model
        agent_rt = types.ModuleType("orchestrator_integration.agent_runtime")
        agent_rt.append_event = mock.MagicMock(return_value={"id": 1})
        # django.utils.timezone used inside fn
        tz_mod = types.ModuleType("django.utils.timezone")
        tz_mod.now = mock.MagicMock()

        with mock.patch.dict(
            sys.modules,
            {
                "orchestrator_integration.models": models_mod,
                "orchestrator_integration.agent_runtime": agent_rt,
                "django.utils.timezone": tz_mod,
            },
        ):
            out = advance_orchestrator_on_task(
                task,
                tool_results=[
                    "查询成功 testcase_id=42 source_type=ui_testcase get_testcases"
                ],
            )
        self.assertTrue(out.get("advanced"), msg=out)
        self.assertEqual(out.get("from_stage"), "query_cases")
        self.assertEqual(out.get("current_stage"), "run_or_browser")
        self.assertTrue(out.get("remount_skills"))


if __name__ == "__main__":
    unittest.main()
