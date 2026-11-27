"""第 1 阶段：任务状态闭环单测。"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class TaskStateClosureTests(TestCase):
    def _task(self, *, stages=3, completed=0, used=2, max_actions=50, status="running"):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        user = User.objects.create_user(
            username=f"closure_{AgentTask.objects.count()}", password="x"
        )
        chat = ChatSession.objects.create(
            user=user, session_id=f"sess-c-{AgentTask.objects.count()}", title="t"
        )
        stage_plan = [
            {"stage_id": "query_cases"},
            {"stage_id": "run_or_browser"},
            {"stage_id": "assert_and_deliver"},
        ][:stages]
        task = AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id=f"t{AgentTask.objects.count():08d}",
            goal="三步验收任务",
            status=status,
            current_stage=stage_plan[0]["stage_id"] if stage_plan else "",
            stage_plan=stage_plan,
            budget={
                "expected_business_steps": stages,
                "completed_business_steps": completed,
                "used_tool_actions": used,
                "max_tool_actions": max_actions,
            },
            facts={
                "completed_stage_ids": [
                    s["stage_id"] for s in stage_plan[:completed]
                ]
            },
        )
        return task

    def test_early_final_answer_cannot_stay_running(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=1, stages=3)
        result = close_task_after_stream(
            task,
            acceptance={"ok": True, "reasons": ["model claimed done"]},
            auto_continue_attempted=False,
            auto_continue_succeeded=False,
        )
        task.refresh_from_db()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(task.status, "blocked")
        self.assertNotEqual(task.status, "running")
        self.assertTrue(task.error_message)
        acc = task.acceptance or {}
        self.assertFalse(acc.get("ok"))
        codes = [b.get("blocker_code") for b in (acc.get("blockers") or [])]
        self.assertTrue(
            any(
                c
                in {
                    "acceptance_without_checkpoints",
                    "incomplete_checkpoints",
                    "incomplete_after_continue",
                }
                for c in codes
            ),
            codes,
        )

    def test_user_stop_cancels(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task()
        result = close_task_after_stream(task, user_stopped=True)
        task.refresh_from_db()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(task.status, "cancelled")

    def test_interrupt_keeps_waiting_approval(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task()
        result = close_task_after_stream(task, interrupt_detected=True)
        task.refresh_from_db()
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(task.status, "awaiting_approval")

    def test_budget_exhausted_blocks(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=1, used=50, max_actions=50)
        result = close_task_after_stream(task, acceptance=None)
        task.refresh_from_db()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result.get("blocker_code"), "budget_exhausted")
        self.assertEqual(task.status, "blocked")

    def test_tool_error_incomplete_blocks(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=0)
        result = close_task_after_stream(task, tool_error=True)
        task.refresh_from_db()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result.get("blocker_code"), "tool_error_incomplete")

    def test_auto_continue_failed_blocks_with_recovery(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=1)
        result = close_task_after_stream(
            task,
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")
        self.assertEqual(result.get("blocker_code"), "auto_continue_failed")
        self.assertIn("继续", result.get("recovery") or "")

    def test_completed_only_with_all_checkpoints(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=3, stages=3)
        result = close_task_after_stream(
            task,
            acceptance={"ok": True, "reasons": [], "evidence": True},
        )
        task.refresh_from_db()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(task.status, "completed")

    def test_refresh_sees_persisted_blocked_and_event(self):
        from orchestrator_integration.models import TaskEvent
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task(completed=0)
        close_task_after_stream(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")
        evts = list(
            TaskEvent.objects.filter(task=task).values_list("event_type", flat=True)
        )
        self.assertTrue(
            "task_blocked" in evts or "stream_closure" in evts,
            evts,
        )

    def test_stale_running_reconcile(self):
        from orchestrator_integration.task_state_closure import (
            reconcile_stale_running_tasks,
        )

        task = self._task()
        # 租约已过期 + 更新时间过旧
        AgentTask = task.__class__
        AgentTask.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(seconds=1200),
            lease_expires_at=timezone.now() - timedelta(seconds=60),
        )
        out = reconcile_stale_running_tasks(older_than_seconds=900, limit=10)
        task.refresh_from_db()
        self.assertGreaterEqual(out["count"], 1)
        self.assertEqual(task.status, "blocked")

    def test_normalize_aliases(self):
        from orchestrator_integration.task_state_closure import normalize_status

        self.assertEqual(normalize_status("pending"), "queued")
        self.assertEqual(normalize_status("awaiting_approval"), "waiting_approval")
