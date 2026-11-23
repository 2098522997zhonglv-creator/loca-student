"""用例执行区：强制推进 / 过早 blocked / 验收需有执行证据。"""

from __future__ import annotations

import json
import unittest

from orchestrator_integration.execute_flow_guard import (
    FORCE_RUN_NUDGE,
    analyze_execute_progress,
    append_force_run_nudge,
    has_execute_run_evidence,
    is_premature_blocked,
    should_nudge_force_run,
)
from orchestrator_integration.execution_policy import evaluate_post_condition
from orchestrator_integration.intent_router import (
    apply_intent_routing,
    decide_kb_access_policy,
    is_testcase_execute_intent,
)


class ExecuteFlowGuardUnitTests(unittest.TestCase):
    def test_discovery_without_run_needs_nudge(self):
        texts = [
            'python loca_stude_tools.py --action get_testcases --module_id 28',
            '[{"case_id": 487, "case_name": "SKU同步"}]',
            'python ui_automation_tools.py --action get_env_configs',
            "[]",
        ]
        progress = analyze_execute_progress(texts)
        self.assertTrue(progress["has_cases"])
        self.assertFalse(progress["has_run"])
        self.assertGreaterEqual(progress["discovery_hits"], 2)
        self.assertTrue(should_nudge_force_run(progress))
        self.assertTrue(is_premature_blocked(progress))

    def test_execute_testcase_counts_as_run(self):
        texts = [
            '[{"case_id": 487}]',
            "python ui_automation_tools.py --action execute_testcase "
            "--testcase_id 40 --wait_result",
            "record_id=99 终态 passed",
        ]
        progress = analyze_execute_progress(texts)
        self.assertTrue(progress["has_run"])
        self.assertFalse(should_nudge_force_run(progress))
        self.assertFalse(is_premature_blocked(progress))
        self.assertTrue(has_execute_run_evidence(texts))

    def test_playwright_goto_counts_as_run(self):
        texts = [
            'get_testcases [{"case_id": 1}]',
            'node run.js "await page.goto(\'https://test.example/\')"',
        ]
        self.assertTrue(analyze_execute_progress(texts)["has_run"])

    def test_append_nudge_once(self):
        out = append_force_run_nudge("ok", enabled=True)
        self.assertIn("【编排强制推进】", out)
        again = append_force_run_nudge(out, enabled=True)
        self.assertEqual(again.count("【编排强制推进】"), 1)
        self.assertIn("execute_testcase", FORCE_RUN_NUDGE)


class ExecuteKbPolicyTests(unittest.TestCase):
    def test_execute_keeps_knowledge_search_for_env(self):
        class _T:
            def __init__(self, name):
                self.name = name

        text = "只执行 sku素材库素材同步用例"
        self.assertTrue(is_testcase_execute_intent(text))
        policy = decide_kb_access_policy(text)
        self.assertEqual(policy.reason, "testcase_execute")
        self.assertTrue(policy.skip_prefetch)
        self.assertTrue(policy.allow_knowledge_search)
        _, kept, hint, policy2 = apply_intent_routing(
            text,
            [_T("knowledge_search"), _T("execute_skill_script"), _T("read_skill_content")],
            kb_active=True,
        )
        self.assertEqual(policy2.reason, "testcase_execute")
        self.assertIn("knowledge_search", [t.name for t in kept])
        self.assertIn("execute_testcase", hint)
        self.assertIn("禁止 report_task_blocked", hint)


class ExecutePostconditionTests(unittest.TestCase):
    def test_query_only_fails_postcondition(self):
        verdict = evaluate_post_condition(
            post_condition="执行完成",
            tool_results=[
                'get_testcases [{"case_id": 487, "case_name": "SKU"}]',
                "get_env_configs []",
            ],
            playbook_id="testcase_execute",
        )
        self.assertFalse(verdict.get("ok"))
        self.assertTrue(
            any("未执行用例" in r for r in (verdict.get("reasons") or []))
        )

    def test_execute_evidence_passes(self):
        verdict = evaluate_post_condition(
            post_condition="执行完成",
            tool_results=[
                "execute_testcase --testcase_id 40 --wait_result record_id=1 终态 passed"
            ],
            playbook_id="testcase_execute",
        )
        self.assertTrue(verdict.get("ok"), msg=verdict)


class PrematureBlockedToolTests(unittest.TestCase):
    def test_report_task_blocked_rejects_before_run(self):
        from orchestrator_integration.builtin_tools.meta_tools import get_meta_tools
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import resolve_playbook
        from orchestrator_integration.skill_zones import ZONE_EXECUTE, resolve_skill_zone
        from orchestrator_integration.task_commission import commission_task
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        text = "只执行 sku素材库素材同步用例"
        d = classify_user_intent(text)
        p = decide_kb_access_policy(text, d)
        z = resolve_skill_zone(text, d, p)
        pb = resolve_playbook(text, z, d, p)
        c = commission_task(
            message=text, zone=z, playbook=pb, decision=d, kb_policy=p
        )
        c.last_tool_results.append(
            'get_testcases [{"case_id": 487, "case_name": "SKU同步"}]'
        )
        set_telemetry_context(
            TelemetryContext(
                zone=ZONE_EXECUTE,
                commission=c,
                playbook=getattr(pb, "playbook_id", "") or "testcase_execute",
            )
        )
        try:
            tools = {t.name: t for t in get_meta_tools()}
            out = tools["report_task_blocked"].invoke(
                {
                    "summary": "UI 用例为空，缺入口 URL case_id=487",
                    "missing_capability": "env_url",
                }
            )
            data = json.loads(out)
            self.assertEqual(data.get("status"), "rejected")
            self.assertEqual(data.get("error_code"), "premature_blocked")
        finally:
            set_telemetry_context(None)


if __name__ == "__main__":
    unittest.main()
