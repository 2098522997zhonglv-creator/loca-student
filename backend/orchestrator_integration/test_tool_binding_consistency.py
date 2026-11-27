"""编排公布工具 ↔ Agent 实际绑定一致性。"""

from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


class ToolBindingConsistencyTests(SimpleTestCase):
    def test_remounts_execute_dropped_by_legacy_intent_filter(self):
        from orchestrator_integration.tool_binding_consistency import (
            ensure_orchestrator_tools_bound,
        )

        orch = SimpleNamespace(
            builtin_allow={
                "execute_skill_script",
                "read_skill_content",
                "assert_task_postcondition",
                "report_task_blocked",
            },
            decision=SimpleNamespace(layer1_mount=["execute_skill_script", "loca-stude"]),
        )
        # 意图裁剪后只剩 KB + read
        tools = [_FakeTool("knowledge_search"), _FakeTool("read_skill_content")]
        pool = [
            _FakeTool("execute_skill_script"),
            _FakeTool("read_skill_content"),
            _FakeTool("assert_task_postcondition"),
            _FakeTool("report_task_blocked"),
            _FakeTool("request_executor_heal"),
        ]
        report = ensure_orchestrator_tools_bound(
            tools, orch_mount=orch, builtin_tool_pool=pool, require_execute=True
        )
        names = {t.name for t in report["tools"]}
        self.assertIn("execute_skill_script", names)
        self.assertIn("execute_skill_script", report["remounted"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["missing"], [])

    def test_missing_execute_from_empty_pool_is_not_ok(self):
        from orchestrator_integration.tool_binding_consistency import (
            ensure_orchestrator_tools_bound,
            executor_gap_block_payload,
        )

        orch = SimpleNamespace(
            builtin_allow={"execute_skill_script", "read_skill_content"},
            decision=SimpleNamespace(layer1_mount=["execute_skill_script"]),
        )
        report = ensure_orchestrator_tools_bound(
            [_FakeTool("knowledge_search")],
            orch_mount=orch,
            builtin_tool_pool=[],
            require_execute=True,
        )
        self.assertFalse(report["ok"])
        self.assertIn("execute_skill_script", report["missing"])
        gap = executor_gap_block_payload(
            missing=report["missing"], remounted=report["remounted"]
        )
        self.assertEqual(gap["blocker_code"], "executor_bind_mismatch")
        self.assertIn("禁止要求用户", gap["recovery"])
        self.assertNotIn("重新下发", gap["recovery"].split("禁止")[0])

    def test_advertise_hint_matches_bound_names_only(self):
        from orchestrator_integration.tool_binding_consistency import (
            build_bound_tools_advertise_hint,
            patch_advertise_hint_for_bound_tools,
        )

        mounted = ["knowledge_search", "execute_skill_script", "read_skill_content"]
        adv = build_bound_tools_advertise_hint(
            mounted, skill_allowlist=["loca-stude"]
        )
        self.assertIn("execute_skill_script", adv)
        self.assertIn("loca-stude", adv)
        self.assertIn("禁止要求用户重新挂载", adv)

        stale = (
            "# 本轮意图路由\n"
            "- 未挂载 execute_skill_script；不要编造落库。\n"
            "- 请明确请用户重说「保存到平台」。\n"
            "- 本轮未挂载执行工具，仅知识库/只读能力可用。\n"
        )
        patched = patch_advertise_hint_for_bound_tools(stale, mounted)
        self.assertNotIn("未挂载 execute_skill_script", patched)
        self.assertNotIn("请明确请用户重说", patched)
        self.assertIn("已绑定执行工具", patched)

    def test_orch_requires_execute_from_builtin_allow(self):
        from orchestrator_integration.tool_binding_consistency import (
            orch_requires_execute,
        )

        self.assertTrue(
            orch_requires_execute(
                SimpleNamespace(
                    builtin_allow={"execute_skill_script"},
                    decision=SimpleNamespace(layer1_mount=[]),
                )
            )
        )
        self.assertFalse(
            orch_requires_execute(
                SimpleNamespace(
                    builtin_allow={"read_skill_content"},
                    decision=SimpleNamespace(layer1_mount=["knowledge_search"]),
                )
            )
        )

    def test_intent_hint_no_longer_asks_user_to_resubmit(self):
        from orchestrator_integration.intent_router import (
            IntentDecision,
            build_intent_hint,
        )

        d = IntentDecision(intents={"qa"}, primary="qa", reason="test")
        self.assertTrue(d.pure_kb_qa)
        hint = build_intent_hint(d, kb_active=True)
        self.assertNotIn("请明确请用户重说", hint)
        self.assertIn("禁止要求用户重新下发任务", hint)
