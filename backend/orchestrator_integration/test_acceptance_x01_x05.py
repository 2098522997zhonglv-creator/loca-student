"""二次验收 X01–X05 与 AC07 相邻场景回归。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from orchestrator_integration.execute_flow_guard import has_execute_run_evidence
from orchestrator_integration.execution_policy import evaluate_post_condition
from orchestrator_integration.task_orchestrator.node_runtime import (
    orch_node_acceptance,
)


class X01X02AssertionAcceptance(unittest.TestCase):
    def test_x01_failed_assertion_item_rejected(self):
        payload = {
            "status": "success",
            "assertions": [
                {
                    "name": "preview matches image",
                    "passed": False,
                    "expected": "image-A",
                    "actual": "placeholder",
                }
            ],
        }
        v = orch_node_acceptance("assert_and_deliver", [json.dumps(payload)])
        self.assertFalse(v["ok"], msg=v)
        self.assertEqual(v["reason"], "assert_failed")

    def test_x02_empty_assertions_rejected(self):
        v = orch_node_acceptance(
            "assert_and_deliver",
            [json.dumps({"status": "success", "assertions": []})],
        )
        self.assertFalse(v["ok"], msg=v)

    def test_positive_assertions_pass(self):
        v = orch_node_acceptance(
            "assert_and_deliver",
            [
                json.dumps(
                    {
                        "status": "success",
                        "test_result": "passed",
                        "assertions": [
                            {"name": "preview", "passed": True},
                            {"name": "cart", "passed": True},
                        ],
                    }
                )
            ],
        )
        self.assertTrue(v["ok"], msg=v)


class X03LatestAttemptWins(unittest.TestCase):
    def test_fallback_success_after_primary_failure(self):
        v = orch_node_acceptance(
            "query_cases",
            [
                json.dumps(
                    {
                        "status": "failed",
                        "error": "primary temporarily unavailable",
                    }
                ),
                json.dumps(
                    {
                        "status": "success",
                        "case_id": 9,
                        "source_type": "ui_testcase",
                    }
                ),
            ],
        )
        self.assertTrue(v["ok"], msg=v)
        self.assertEqual(v["reason"], "case_ref_found")
        self.assertEqual(str(v["ids"].get("case_id")), "9")


class X05BoundRequired(unittest.TestCase):
    def test_reject_before_bind(self):
        skill_dir = (
            Path(__file__).resolve().parents[2]
            / "bundled_skills"
            / "ui-automation-skill"
        )
        sys.path.insert(0, str(skill_dir))
        try:
            import ui_automation_tools as ui  # type: ignore

            self.assertFalse(
                ui.should_accept_case_result(
                    case_id=9,
                    testcase_id=9,
                    result_record_id=22,
                    result_execution_id="other-22",
                    bound_record_id=None,
                    bound_execution_id=None,
                )
            )
        finally:
            sys.path = [p for p in sys.path if p != str(skill_dir)]


class X04EmptyParamRow(unittest.TestCase):
    def test_explicit_empty_row_kept_in_schedule(self):
        actuator = Path(__file__).resolve().parents[2] / "loca_stude_Actuator"
        sys.path.insert(0, str(actuator))
        try:
            from step_advanced import parse_parametrize_rows

            rows = parse_parametrize_rows([{}, {"user": "alpha"}])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], {})
            self.assertEqual(rows[1].get("user"), "alpha")
            # 调度侧：显式 list 不得丢弃 {}
            raw = [{}, {"user": "alpha"}]
            dict_rows = [r for r in rows if isinstance(r, dict)]
            explicit_list = isinstance(raw, list) and len(raw) >= 1
            schedule = dict_rows if explicit_list else [r for r in dict_rows if r]
            self.assertEqual(len(schedule), 2)
        finally:
            sys.path = [p for p in sys.path if p != str(actuator)]


class AC07TerminalFailedCompletes(unittest.TestCase):
    def test_terminal_failed_is_run_evidence(self):
        text = "execution_id=e1 终态=failed 断言不通过"
        self.assertTrue(has_execute_run_evidence([text]))
        acc = evaluate_post_condition(
            post_condition="exec",
            tool_results=[text],
            playbook_id="testcase_execute",
        )
        self.assertTrue(acc["ok"], msg=acc)


if __name__ == "__main__":
    unittest.main()
