"""D04 配置快照 + D11 事件时间字段。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from langgraph_integration.models import ChatSession


class TaskConfigAndEventTests(TestCase):
    def test_effective_config_separates_business_and_tool_budget(self):
        from orchestrator_integration.task_execution_config import (
            build_effective_config,
            user_visible_config_summary,
            validate_config_overrides,
        )

        stages = [
            {"stage_id": "kb_research", "acceptance": "来源"},
            {"stage_id": "persist", "acceptance": "id"},
            {"stage_id": "execute", "acceptance": "终态"},
            {"stage_id": "summarize", "acceptance": "汇总"},
        ]
        cfg = build_effective_config(stage_plan=stages, overrides={"max_tool_actions": 30})
        self.assertEqual(cfg["expected_business_steps"], 4)
        self.assertEqual(cfg["max_tool_actions"], 30)
        self.assertNotEqual(cfg["expected_business_steps"], cfg["max_tool_actions"])
        self.assertIn("config_hash", cfg)
        summary = user_visible_config_summary(cfg)
        self.assertIn("业务检查点", summary)
        self.assertIn("工具调用", summary)

        bad = validate_config_overrides({"max_tool_actions": -1})
        self.assertFalse(bad["ok"])
        capped = validate_config_overrides({"max_tool_actions": 9999})
        self.assertTrue(capped["ok"])
        self.assertEqual(capped["overrides"]["max_tool_actions"], 200)

    def test_append_event_includes_time_fields(self):
        from orchestrator_integration.agent_runtime import (
            append_event,
            create_or_resume_task,
            task_snapshot,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="cfg_evt", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-cfg-evt", title="t")
        c = TaskCommission(
            task_id="tid-cfg",
            zone_id="execute",
            playbook_id="",
            primary_skills=frozenset({"loca-stude"}),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="heavy",
            max_tool_steps=12,
        )
        stages = [
            {
                "stage_id": "kb_research",
                "goal": "查",
                "tool_capabilities": ["knowledge_search"],
                "depends_on": [],
                "acceptance": "来源",
                "auth_actions": [],
            },
            {
                "stage_id": "persist",
                "goal": "存",
                "tool_capabilities": ["loca-stude"],
                "depends_on": ["kb_research"],
                "acceptance": "id",
                "auth_actions": ["write"],
            },
        ]
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="根据知识库生成并保存",
            commission=c,
            stage_plan=stages,
        )
        self.assertIn("expected_business_steps", task.budget)
        self.assertEqual(task.budget.get("expected_business_steps"), 2)
        self.assertEqual(task.budget.get("max_tool_actions"), 12)

        evt = append_event(task, "step_result", status="running", payload={"n": 1})
        self.assertTrue(evt.get("occurred_at"))
        self.assertTrue(evt.get("observed_at"))
        self.assertTrue(evt.get("time"))
        self.assertEqual(evt.get("schema_version"), 1)
        self.assertIn("config_version", evt)

        snap = task_snapshot(task)
        self.assertIn("effective_config", snap)
        self.assertTrue(snap["events"])
        self.assertTrue(snap["events"][0].get("occurred_at"))
