"""D01/D02：真实动作与 Catalog 一致性；旧名不误拒。"""

from __future__ import annotations

import unittest

from orchestrator_integration.skill_action_sync import (
    consistency_report,
    discover_api_actions,
    discover_ui_actions,
    resolve_action_alias,
    sync_ui_and_api_action_maps,
)
from orchestrator_integration.skill_catalog import (
    catalog_reject_unregistered_write,
    load_skill_catalog,
    reload_skill_catalog,
    resolve_action_side_effect,
)


class SkillActionSyncTests(unittest.TestCase):
    def setUp(self):
        reload_skill_catalog()

    def test_discover_counts(self):
        ui = discover_ui_actions()
        api = discover_api_actions()
        self.assertGreaterEqual(len(ui), 40)
        self.assertGreaterEqual(len(api), 100)
        for must in (
            "create_page_step",
            "set_step_details",
            "create_testcase",
            "execute_testcase",
            "get_execution_record",
        ):
            self.assertIn(must, ui)
        for must in ("run_interface", "run_testcase", "execute_task_suite"):
            self.assertIn(must, api)

    def test_catalog_contains_real_actions_a01(self):
        cat = load_skill_catalog()
        ui_entry = cat.get("ui-automation")
        api_entry = cat.get("api-automation")
        self.assertIsNotNone(ui_entry)
        self.assertIsNotNone(api_entry)
        self.assertIn("create_page_step", ui_entry.actions)
        self.assertIn("execute_testcase", ui_entry.actions)
        self.assertIn("run_interface", api_entry.actions)
        self.assertIn("execute_task_suite", api_entry.actions)
        # 旧失效名不应再是唯一登记项：真实动作必须在
        report_ui = consistency_report(
            skill_name="ui-automation",
            catalog_actions=ui_entry.actions,
            discovered=discover_ui_actions(),
        )
        report_api = consistency_report(
            skill_name="api-automation",
            catalog_actions=api_entry.actions,
            discovered=discover_api_actions(),
        )
        self.assertEqual(report_ui["missing_in_catalog"], [])
        self.assertEqual(report_api["missing_in_catalog"], [])

    def test_real_writes_not_rejected(self):
        for skill, cmd in (
            (
                "ui-automation",
                "python ui_automation_tools.py --action create_page_step --name x",
            ),
            (
                "ui-automation",
                "python ui_automation_tools.py --action set_step_details --step_id 1",
            ),
            (
                "ui-automation",
                "python ui_automation_tools.py --action execute_testcase --testcase_id 1",
            ),
            (
                "api-automation",
                "python api_automation_tools.py --action run_interface --interface_id 1",
            ),
            (
                "api-automation",
                "python api_automation_tools.py --action run_testcase --testcase_id 1",
            ),
            (
                "api-automation",
                "python api_automation_tools.py --action execute_task_suite --project_id 1",
            ),
        ):
            self.assertIsNone(
                catalog_reject_unregistered_write(skill, cmd),
                msg=cmd,
            )

    def test_aliases_resolve(self):
        self.assertEqual(
            resolve_action_alias("ui-automation", "get_ui_cases"), "get_testcases"
        )
        self.assertEqual(
            resolve_action_alias("api-automation", "run_api"), "run_interface"
        )
        effect = resolve_action_side_effect(
            "api-automation",
            "python api_automation_tools.py --action run_api --interface_id 1",
        )
        self.assertEqual(effect, "execute")

    def test_sync_maps_cover_discovered(self):
        maps = sync_ui_and_api_action_maps()
        self.assertTrue(set(discover_ui_actions()) <= set(maps["ui-automation"]))
        self.assertTrue(set(discover_api_actions()) <= set(maps["api-automation"]))


class ExecutionRefTests(unittest.TestCase):
    def test_bind_and_mismatch(self):
        from orchestrator_integration.task_orchestrator.execution_refs import (
            assert_same_execution,
            bind_execution_ref,
            call_success_is_not_test_pass,
            make_case_ref,
        )

        facts = bind_execution_ref(
            {},
            execution_id="e-1",
            record_id=99,
            case_ref=make_case_ref(source_type="ui_testcase", case_id=40, project_id=1),
        )
        self.assertEqual(facts["execution_ref"]["execution_id"], "e-1")
        self.assertIsNotNone(assert_same_execution(facts, execution_id="e-2"))
        self.assertIsNone(assert_same_execution(facts, execution_id="e-1", record_id=99))
        msg = call_success_is_not_test_pass(True, None)
        self.assertIn("不是测试通过", msg)


if __name__ == "__main__":
    unittest.main()
