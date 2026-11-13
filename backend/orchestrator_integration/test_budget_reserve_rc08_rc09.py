"""预算预留结算 + RC08 租约矩阵 + RC09 大事件分页。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import (
    acquire_lease,
    append_event,
    consume_budget,
    create_or_resume_task,
    heartbeat_lease,
    release_lease,
    release_tool_reservation,
    reserve_tool_actions,
    settle_reserved_action,
    task_snapshot,
    try_claim_execution,
)
from orchestrator_integration.task_commission import TaskCommission


def _commission(tid: str, max_steps: int = 8) -> TaskCommission:
    return TaskCommission(
        task_id=tid,
        zone_id="browser",
        playbook_id="browser_act",
        primary_skills=frozenset({"playwright-skill"}),
        auxiliary_skills=frozenset(),
        short_circuit=True,
        draft_writes=False,
        channel="light",
        max_tool_steps=max_steps,
    )


class BudgetReserveSettleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rsv_u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rsv", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="batch",
            commission=_commission("tid-rsv", max_steps=3),
            lease_owner="w1",
        )
        b = dict(self.task.budget or {})
        b["max_tool_actions"] = 3
        b["max_tool_steps"] = 3
        b["used_tool_actions"] = 0
        b["reserved_tool_actions"] = 0
        self.task.budget = b
        self.task.save(update_fields=["budget"])

    def test_reserve_settle_release_unused(self):
        ok, took, _ = reserve_tool_actions(self.task, 5)
        self.assertTrue(ok)
        self.assertEqual(took, 3)  # 只能预留上限
        self.task.refresh_from_db()
        self.assertEqual(int(self.task.budget["reserved_tool_actions"]), 3)

        # 启动 2 次 → 实耗 2，预留剩 1
        self.assertTrue(settle_reserved_action(self.task)[0])
        self.assertTrue(settle_reserved_action(self.task)[0])
        self.task.refresh_from_db()
        self.assertEqual(int(self.task.budget["used_tool_actions"]), 2)
        self.assertEqual(int(self.task.budget["reserved_tool_actions"]), 1)

        # 未启动预留释放 → 不计实耗
        released = release_tool_reservation(self.task)
        self.assertEqual(released, 1)
        self.task.refresh_from_db()
        self.assertEqual(int(self.task.budget["used_tool_actions"]), 2)
        self.assertEqual(int(self.task.budget["reserved_tool_actions"]), 0)

        # 预留占满后不可再 consume
        reserve_tool_actions(self.task, 1)
        bad, msg = consume_budget(self.task, tool_steps=1)
        self.assertFalse(bad)
        self.assertIn("budget_tool", msg)


class Rc08LeaseMatrixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc08m", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc08m", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="lease",
            commission=_commission("tid-rc08m"),
            lease_owner="owner-a",
        )

    def test_dual_claim_only_one_owner(self):
        self.assertTrue(acquire_lease(self.task, "owner-a"))
        claim_b = try_claim_execution(self.task, "owner-b")
        self.assertFalse(claim_b.get("acquired"))
        self.assertEqual(claim_b.get("error_code"), "lease_held")
        self.assertTrue(heartbeat_lease(self.task, "owner-a"))
        self.assertFalse(heartbeat_lease(self.task, "owner-b"))

    def test_expired_lease_can_be_reclaimed(self):
        self.task.lease_owner = "owner-a"
        self.task.lease_expires_at = timezone.now() - timedelta(seconds=5)
        self.task.save(update_fields=["lease_owner", "lease_expires_at"])
        claim = try_claim_execution(self.task, "owner-b")
        self.assertTrue(claim.get("acquired"), claim)
        self.assertEqual(claim.get("lease_owner"), "owner-b")


class Rc09LargeEventPagingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc09m", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc09m", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="events",
            commission=_commission("tid-rc09m"),
            lease_owner="w9",
        )

    def test_page_over_250_events_no_dup_sequence(self):
        for i in range(260):
            append_event(self.task, "step_result", status="running", payload={"i": i})

        page1 = task_snapshot(self.task, limit=100)
        self.assertTrue(page1.get("has_more"))
        seqs1 = [e["sequence"] for e in page1["events"]]
        self.assertEqual(len(seqs1), len(set(seqs1)))
        cursor = page1["next_cursor"]
        self.assertTrue(cursor)

        page2 = task_snapshot(self.task, after_event_id=cursor, limit=100)
        seqs2 = [e["sequence"] for e in page2["events"]]
        self.assertTrue(seqs2)
        self.assertTrue(min(seqs2) > max(seqs1))
        overlap = set(seqs1) & set(seqs2)
        self.assertFalse(overlap)

        # 继续翻到尾
        seen = set(seqs1) | set(seqs2)
        cursor = page2["next_cursor"]
        guard = 0
        while page2.get("has_more") and cursor and guard < 10:
            page2 = task_snapshot(self.task, after_event_id=cursor, limit=100)
            for s in (e["sequence"] for e in page2["events"]):
                self.assertNotIn(s, seen)
                seen.add(s)
            cursor = page2.get("next_cursor") or ""
            guard += 1
        self.assertGreaterEqual(len(seen), 260)
