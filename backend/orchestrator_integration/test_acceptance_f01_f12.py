"""验收报告 F01–F12 对应的回归探针（离线函数级）。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from orchestrator_integration.task_orchestrator.execution_refs import bind_execution_ref
from orchestrator_integration.task_orchestrator.gates import assert_cross_tool_object_ids
from orchestrator_integration.task_orchestrator.node_runtime import (
    extract_execution_ids,
    merge_ids_into_facts,
    orch_node_acceptance,
)


class F01F02TestResultSemantics(unittest.TestCase):
    def test_f01_outer_success_is_not_passed(self):
        facts, note = merge_ids_into_facts(
            {},
            {"status": "success"},
            skill_name="loca-stude",
            tool_succeeded=True,
        )
        self.assertNotEqual(facts.get("test_result"), "passed")
        self.assertIsNone((facts.get("execution_ref") or {}).get("test_result"))
        self.assertIn("不是测试通过", note)

    def test_f02_inner_failed_overrides_outer_success(self):
        text = json.dumps(
            {
                "status": "success",
                "data": {
                    "execution_id": "run-1",
                    "status": "failed",
                    "failed_steps": 1,
                },
            }
        )
        ids = extract_execution_ids(text)
        facts, _ = merge_ids_into_facts(
            {}, ids, skill_name="ui-automation", tool_succeeded=True
        )
        self.assertEqual(facts.get("test_result"), "failed")
        self.assertEqual(facts["execution_id"], "run-1")


class F08NewExecutionClearsPass(unittest.TestCase):
    def test_rebind_does_not_inherit_passed(self):
        facts = {
            "test_result": "passed",
            "execution_ref": {
                "execution_id": "old-1",
                "record_id": 11,
                "status": "completed",
                "test_result": "passed",
            },
            "execution_id": "old-1",
            "record_id": 11,
        }
        facts2, _ = merge_ids_into_facts(
            facts,
            {"execution_id": "own-22", "record_id": 22},
            skill_name="ui-automation",
            tool_succeeded=True,
        )
        self.assertIsNone(facts2.get("test_result"))
        self.assertIsNone(facts2["execution_ref"].get("test_result"))
        self.assertEqual(facts2["execution_ref"]["execution_id"], "own-22")
        self.assertTrue(facts2.get("execution_history"))


class F03F06NodeAcceptance(unittest.TestCase):
    def test_f03_customize_upload_accepts_structured_ok(self):
        v = orch_node_acceptance(
            "customize_upload",
            ['{"upload_ok": true, "preview_ok": true}'],
        )
        self.assertTrue(v["ok"], msg=v)
        self.assertEqual(v["reason"], "upload_preview_ok")

    def test_f03_unknown_without_evidence_still_fails_empty(self):
        v = orch_node_acceptance("customize_upload", ["{}"])
        self.assertFalse(v["ok"])

    def test_f04_query_failure_not_accepted(self):
        v = orch_node_acceptance(
            "query_cases",
            [
                json.dumps(
                    {
                        "status": "failed",
                        "error": "get_testcases failed: service unavailable",
                    }
                )
            ],
        )
        self.assertFalse(v["ok"], msg=v)
        self.assertEqual(v["reason"], "query_failed")

    def test_f05_add_to_cart_failure_rejected(self):
        v = orch_node_acceptance(
            "add_to_cart",
            [json.dumps({"status": "failed", "error": "out of stock"})],
        )
        self.assertFalse(v["ok"], msg=v)

    def test_f05_add_to_cart_needs_cart_id(self):
        v = orch_node_acceptance("add_to_cart", ['{"status":"success"}'])
        self.assertFalse(v["ok"])
        v2 = orch_node_acceptance(
            "add_to_cart", ['{"status":"success","cart_id":"C-1"}']
        )
        self.assertTrue(v2["ok"], msg=v2)

    def test_f06_assert_missing_rejected(self):
        v = orch_node_acceptance(
            "assert_and_deliver",
            [json.dumps({"status": "failed", "message": "assertions missing"})],
        )
        self.assertFalse(v["ok"], msg=v)


class F07CrossToolIds(unittest.TestCase):
    def test_order_sn_mismatch_detected(self):
        err = assert_cross_tool_object_ids(
            {"order_sn": "ORDER-A"},
            '{"order_sn":"ORDER-B"}',
        )
        self.assertIsNotNone(err)
        self.assertIn("ORDER-A", err)
        self.assertIn("ORDER-B", err)

    def test_cart_id_extracted(self):
        ids = extract_execution_ids('{"cart_id":"CART-9","order_sn":"SN-1"}')
        self.assertEqual(ids.get("cart_id"), "CART-9")
        self.assertEqual(ids.get("order_sn"), "SN-1")


class F12OutputKeysPersist(unittest.TestCase):
    def test_entry_and_extracted_vars_saved(self):
        ids = extract_execution_ids(
            json.dumps(
                {
                    "status": "success",
                    "entry_url": "https://shop.example/",
                    "product_url": "https://shop.example/p/1",
                    "test_image": "/tmp/a.png",
                    "extracted_vars": {"sku": "S1"},
                }
            )
        )
        facts, note = merge_ids_into_facts(
            {}, ids, skill_name="playwright-skill", tool_succeeded=True
        )
        self.assertEqual(facts.get("entry_url"), "https://shop.example/")
        self.assertEqual(facts.get("product_url"), "https://shop.example/p/1")
        self.assertEqual(facts.get("test_image"), "/tmp/a.png")
        self.assertEqual(facts.get("extracted_vars"), {"sku": "S1"})
        self.assertNotEqual(facts.get("test_result"), "passed")
        self.assertIn("不是测试通过", note)
        self.assertIn("entry_url", facts.get("fact_sources") or {})


class F09CallbackFilter(unittest.TestCase):
    def test_reject_other_record_for_same_case(self):
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
                    bound_record_id=11,
                    bound_execution_id="own-11",
                )
            )
            self.assertTrue(
                ui.should_accept_case_result(
                    case_id=9,
                    testcase_id=9,
                    result_record_id=11,
                    result_execution_id="own-11",
                    bound_record_id=11,
                    bound_execution_id="own-11",
                )
            )
        finally:
            sys.path = [p for p in sys.path if p != str(skill_dir)]


class F10F11Parametrize(unittest.TestCase):
    def test_single_row_injects_and_rows_isolate(self):
        actuator = Path(__file__).resolve().parents[2] / "loca_stude_Actuator"
        sys.path.insert(0, str(actuator))
        try:
            from data_processor import DataProcessor
            from step_advanced import parse_parametrize_rows

            rows = parse_parametrize_rows([{"user": "alpha"}])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["user"], "alpha")

            base = DataProcessor()
            base.set_cache("shared", "base")
            row1 = base.fork()
            row1.set_cache("user", "alpha")
            row1.set_cache("extracted_token", "t-from-row1")
            row2 = base.fork()
            row2.set_cache("user", "beta")
            self.assertEqual(row2.get_cache("shared"), "base")
            self.assertIsNone(row2.get_cache("extracted_token"))
            self.assertEqual(row1.get_cache("extracted_token"), "t-from-row1")
        finally:
            sys.path = [p for p in sys.path if p != str(actuator)]


if __name__ == "__main__":
    unittest.main()
