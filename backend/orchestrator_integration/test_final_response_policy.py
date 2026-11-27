from types import SimpleNamespace

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from orchestrator_integration.final_response_policy import (
    correction_for_executor_deferral,
    requests_user_to_remount_executor,
    should_continue_incomplete_task,
)


class FinalResponsePolicyTests(SimpleTestCase):
    def test_continues_running_task_with_remaining_checkpoints_and_budget(self):
        self.assertTrue(
            should_continue_incomplete_task(
                executor_mounted=True,
                user_stopped=False,
                interrupt_detected=False,
                task_status="running",
                used_tool_actions=2,
                reserved_tool_actions=0,
                max_tool_actions=20,
                completed_business_steps=1,
                expected_business_steps=3,
            )
        )

    def test_does_not_continue_when_stopped_interrupted_or_not_running(self):
        base = {
            "executor_mounted": True,
            "user_stopped": False,
            "interrupt_detected": False,
            "task_status": "running",
            "used_tool_actions": 2,
            "reserved_tool_actions": 0,
            "max_tool_actions": 20,
            "completed_business_steps": 1,
            "expected_business_steps": 3,
        }
        for change in (
            {"user_stopped": True},
            {"interrupt_detected": True},
            {"task_status": "waiting_approval"},
            {"task_status": "completed"},
        ):
            with self.subTest(change=change):
                self.assertFalse(should_continue_incomplete_task(**(base | change)))

    def test_does_not_continue_without_executor_budget_or_remaining_work(self):
        base = {
            "executor_mounted": True,
            "user_stopped": False,
            "interrupt_detected": False,
            "task_status": "running",
            "used_tool_actions": 2,
            "reserved_tool_actions": 0,
            "max_tool_actions": 20,
            "completed_business_steps": 1,
            "expected_business_steps": 3,
        }
        for change in (
            {"executor_mounted": False},
            {"used_tool_actions": 20},
            {"reserved_tool_actions": 18},
            {"completed_business_steps": 3},
            {"expected_business_steps": 0},
        ):
            with self.subTest(change=change):
                self.assertFalse(should_continue_incomplete_task(**(base | change)))

    def test_detects_user_remount_request(self):
        self.assertTrue(
            requests_user_to_remount_executor(
                "需要你做的一件事：为本轮重新挂载执行类工具 execute_skill_script。"
            )
        )

    def test_detects_user_trigger_or_resend_deferral_from_reported_dialog(self):
        self.assertTrue(
            requests_user_to_remount_executor(
                "需要你这边触发一下，再发一句带目标的话就会自动切回浏览器工具。"
            )
        )
        self.assertTrue(
            requests_user_to_remount_executor(
                "请回一句继续执行回归，服务端就会恢复工具并跑完。"
            )
        )

    def test_does_not_detect_neutral_tool_status(self):
        self.assertFalse(
            requests_user_to_remount_executor(
                "本轮未完成执行，服务端记录显示工具调用为 0。"
            )
        )

    def test_correction_uses_observed_mount_state(self):
        self.assertIn("已挂载", correction_for_executor_deferral(executor_mounted=True))
        self.assertIn("没有可用的执行工具", correction_for_executor_deferral(executor_mounted=False))

    async def test_correction_replaces_checkpointed_assistant_message(self):
        from orchestrator_integration.agent_loop_view import (
            _persist_corrected_final_message,
        )

        original = AIMessage(content="请重新挂载 execute_skill_script", id="assistant-final")

        class FakeAgent:
            nodes = {"model": object()}

            def __init__(self):
                self.update = None

            async def aget_state(self, _config):
                return SimpleNamespace(
                    values={"messages": [HumanMessage(content="继续任务"), original]}
                )

            async def aupdate_state(self, _config, values, as_node=None):
                self.update = (values, as_node)

        agent = FakeAgent()
        corrected = "服务端将继续处理本任务。"
        persisted = await _persist_corrected_final_message(
            agent,
            {"configurable": {"thread_id": "test"}},
            original.content,
            corrected,
            "test",
        )

        self.assertTrue(persisted)
        payload, node = agent.update
        self.assertEqual(node, "model")
        self.assertIsInstance(payload["messages"][0], RemoveMessage)
        self.assertEqual(payload["messages"][-1].content, corrected)
        self.assertEqual(payload["messages"][-1].id, "assistant-final")
