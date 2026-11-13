"""质量指标 HTTP API（FR10.4）。"""
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from orchestrator_integration.agent_loop_view import AgentQualityMetricsAPIView


@override_settings(ALLOWED_HOSTS=["*", "testserver"])
class AgentQualityMetricsAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="metrics_u", password="x")
        self.token = str(RefreshToken.for_user(self.user).access_token)
        self.factory = RequestFactory()

    def test_metrics_requires_auth(self):
        req = self.factory.get("/api/orchestrator/agent-loop/metrics/")
        resp = async_to_sync(AgentQualityMetricsAPIView.as_view())(req)
        self.assertEqual(resp.status_code, 401)

    def test_metrics_ok_with_bearer(self):
        req = self.factory.get(
            "/api/orchestrator/agent-loop/metrics/",
            {"scope": "user", "limit": "50"},
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        resp = async_to_sync(AgentQualityMetricsAPIView.as_view())(req)
        self.assertEqual(resp.status_code, 200)
        import json

        body = json.loads(resp.content.decode("utf-8"))
        self.assertEqual(body["status"], "success")
        self.assertIn("tasks_sampled", body["data"])
        self.assertIn("telemetry", body["data"])
