"""RC23：部署版本与运行指标可回读。"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import (
    quality_metrics,
    runtime_deploy_info,
)
from orchestrator_integration.models import AgentTask


class Rc23DeployMetricsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc23u", password="x")
        self.other = User.objects.create_user(username="rc23o", password="x")
        self.chat_a = ChatSession.objects.create(
            user=self.user, session_id="sess-rc23-a", title="a"
        )
        self.chat_b = ChatSession.objects.create(
            user=self.other, session_id="sess-rc23-b", title="b"
        )
        AgentTask.objects.create(
            user=self.user,
            session=self.chat_a,
            public_task_id="t-rc23-a",
            goal="g",
            status="blocked",
            facts={"no_progress_streak": 3},
        )
        AgentTask.objects.create(
            user=self.other,
            session=self.chat_b,
            public_task_id="t-rc23-b",
            goal="g2",
            status="completed",
            acceptance={"ok": False},
        )

    def test_deploy_info_has_versions(self):
        info = runtime_deploy_info()
        self.assertIn("git_sha", info)
        self.assertIn("config_schema_version", info)
        self.assertEqual(info.get("service"), "orchestrator_agent_loop")

    def test_metrics_scoped_by_user(self):
        mine = quality_metrics(user_id=self.user.id)
        self.assertEqual(mine["tasks_sampled"], 1)
        self.assertEqual((mine.get("status_counts") or {}).get("blocked"), 1)
        self.assertIn("deploy", mine)
        self.assertIn("no_progress_count", mine)
        self.assertIn("reconfirm_count", mine)
        self.assertEqual(
            mine["deploy"].get("config_schema_version"),
            runtime_deploy_info()["config_schema_version"],
        )

        other = quality_metrics(user_id=self.other.id)
        self.assertEqual(other["tasks_sampled"], 1)
        self.assertEqual(other.get("false_complete_count"), 1)
