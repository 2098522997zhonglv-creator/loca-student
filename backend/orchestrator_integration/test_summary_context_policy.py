"""上下文摘要：不泄漏为用户消息；压缩保留结构化任务状态。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from langchain_core.messages import HumanMessage, SystemMessage


class SummaryContextPolicyAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 摘要上下文策略验收 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def test_v1_summary_not_human_message(self):
        """摘要写入应为 SystemMessage，且带 ui_hidden / lc_source。"""
        from orchestrator_integration.summary_context_policy import (
            InternalSummarizationMiddleware,
            is_summarization_artifact_message,
        )

        leak = HumanMessage(
            content=(
                "Here is a summary of the conversation to date:\n\n"
                "Previous conversation was too long to summarize."
            ),
            additional_kwargs={"lc_source": "summarization"},
        )
        built = InternalSummarizationMiddleware._build_new_messages(
            "Previous conversation was too long to summarize."
        )
        self._record(
            "SUM-V1-not-user-bubble",
            is_summarization_artifact_message(leak) is True
            and len(built) == 1
            and isinstance(built[0], SystemMessage)
            and not isinstance(built[0], HumanMessage)
            and (built[0].additional_kwargs or {}).get("ui_hidden") is True
            and (built[0].additional_kwargs or {}).get("lc_source") == "summarization"
            and "非用户消息" in str(built[0].content),
            f"built_type={type(built[0]).__name__} leak={is_summarization_artifact_message(leak)}",
        )

    def test_v2_enrich_keeps_task_structure(self):
        """压缩失败时仍保留目标/检查点/进度/授权结构。"""
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.summary_context_policy import (
            enrich_summary_text,
        )

        User = get_user_model()
        user = User.objects.create_user(username="sum_pol_0", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id="sum-sess-0", title="sum"
        )
        AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id="sum00001",
            goal="常规品功能回归",
            status="running",
            current_stage="fetch_conditions",
            stage_plan=[
                {"stage_id": "fetch_conditions"},
                {"stage_id": "check_executable"},
                {"stage_id": "verify_deliver"},
            ],
            auth_scope={"granted_skills": ["playwright-skill"]},
            facts={},
            budget={
                "max_tool_actions": 104,
                "used_tool_actions": 84,
                "expected_business_steps": 3,
                "completed_business_steps": 0,
            },
        )
        text = enrich_summary_text(
            "Previous conversation was too long to summarize.",
            session_id="sum-sess-0",
        )
        self._record(
            "SUM-V2-structured-state",
            "常规品功能回归" in text
            and "fetch_conditions" in text
            and "business_progress" in text
            and "0/3" in text
            and "playwright-skill" in text
            and "Here is a summary of the conversation to date" not in text,
            f"text_head={text[:220]!r}",
        )

    def test_v3_history_filter_drops_leak(self):
        """历史条目过滤器丢弃英文摘要泄漏。"""
        from orchestrator_integration.summary_context_policy import (
            filter_history_message_dict,
        )

        leak = {
            "type": "human",
            "content": (
                "Here is a summary of the conversation to date:\n\n"
                "Previous conversation was too long to summarize."
            ),
        }
        ok_msg = {"type": "human", "content": "请继续常规品回归"}
        self._record(
            "SUM-V3-history-filter",
            filter_history_message_dict(leak) is None
            and filter_history_message_dict(ok_msg) == ok_msg,
            "leak_dropped_and_real_user_kept",
        )
