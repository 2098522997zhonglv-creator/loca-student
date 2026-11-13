"""RC07：pending 持久化后刷新仍可批准；改命令拒绝；只执行一次。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import fingerprint_payload
from orchestrator_integration.models import ApprovalRequest
from orchestrator_integration.write_confirmation import (
    build_write_confirm_interrupt,
    consume_approval,
    execute_approved_pending,
    get_write_confirm_store,
    normalize_pending_commands,
)


class Rc07ApprovalPersistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc07u", password="x")
        self.other = User.objects.create_user(username="rc07o", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc07", title="t"
        )
        self.pending = {
            "skill_name": "loca-stude",
            "command": "python x.py --action add_testcase --title demo",
        }

    def test_refresh_clears_memory_still_consumes_from_db(self):
        interrupt = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07",
            tool_name="execute_skill_script",
            confirmation={
                "status": "needs_confirmation",
                "mode": "confirm",
                "message": "确认写入",
                "pending": self.pending,
            },
        )
        iid = interrupt["interrupt_id"]
        self.assertTrue(ApprovalRequest.objects.filter(interrupt_id=iid).exists())

        # 模拟刷新：清空进程内缓存
        get_write_confirm_store().pop(iid)
        self.assertIsNone(get_write_confirm_store().get(iid))

        consumed = consume_approval(
            iid,
            user_id=self.user.id,
            decision="approve",
            session_id="sess-rc07",
        )
        self.assertTrue(consumed["ok"])
        self.assertEqual(consumed["status"], "consumed")
        pending = (consumed.get("action_args") or {}).get("pending")
        self.assertEqual(pending["command"], self.pending["command"])
        cmds = normalize_pending_commands(pending)
        self.assertEqual(len(cmds), 1)

        # 重复批准拒绝
        again = consume_approval(
            iid, user_id=self.user.id, decision="approve", session_id="sess-rc07"
        )
        self.assertFalse(again["ok"])
        self.assertEqual(again["error_code"], "approval_consumed")

    def test_changed_command_fingerprint_rejected(self):
        interrupt = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07b",
            tool_name="execute_skill_script",
            confirmation={
                "status": "needs_confirmation",
                "pending": self.pending,
            },
        )
        iid = interrupt["interrupt_id"]
        row = ApprovalRequest.objects.get(interrupt_id=iid)
        # 篡改落库命令（改素材/改动作）
        tampered = dict(self.pending)
        tampered["command"] = "python x.py --action add_testcase --title HACKED"
        row.action_args = {"pending": tampered, "mode": "confirm"}
        row.save(update_fields=["action_args", "updated_at"])

        get_write_confirm_store().pop(iid)
        bad = consume_approval(
            iid,
            user_id=self.user.id,
            decision="approve",
            session_id="sess-rc07",
        )
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error_code"], "param_fingerprint_mismatch")

    def test_expired_and_cross_user_denied(self):
        interrupt = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07c",
            tool_name="execute_skill_script",
            confirmation={"status": "needs_confirmation", "pending": self.pending},
        )
        iid = interrupt["interrupt_id"]
        row = ApprovalRequest.objects.get(interrupt_id=iid)
        row.expires_at = timezone.now() - timedelta(seconds=5)
        row.save(update_fields=["expires_at"])

        expired = consume_approval(
            iid, user_id=self.user.id, decision="approve", session_id="sess-rc07"
        )
        self.assertFalse(expired["ok"])
        self.assertEqual(expired["error_code"], "approval_expired")

        interrupt2 = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07d",
            tool_name="execute_skill_script",
            confirmation={"status": "needs_confirmation", "pending": self.pending},
        )
        denied = consume_approval(
            interrupt2["interrupt_id"],
            user_id=self.other.id,
            decision="approve",
            session_id="sess-rc07",
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error_code"], "permission_denied")

    def test_execute_approved_empty_pending_fails(self):
        r = execute_approved_pending({}, user_id=self.user.id)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "empty_pending")

    def test_interrupt_exposes_original_action(self):
        interrupt = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07e",
            tool_name="execute_skill_script",
            confirmation={
                "status": "needs_confirmation",
                "message": "写库",
                "pending": self.pending,
            },
        )
        args = interrupt["action_requests"][0]["args"]
        self.assertIn("pending", args)
        self.assertEqual(args["pending"]["command"], self.pending["command"])
        self.assertIn("original_action", args)
        self.assertIn("add_testcase", args["original_action"])
        fp = fingerprint_payload(self.pending)
        row = ApprovalRequest.objects.get(interrupt_id=interrupt["interrupt_id"])
        self.assertEqual(row.param_fingerprint, fp)

    def test_snapshot_includes_pending_approval(self):
        from orchestrator_integration.agent_runtime import (
            create_or_resume_task,
            park_for_approval,
            task_snapshot,
        )
        from orchestrator_integration.task_commission import TaskCommission

        interrupt = build_write_confirm_interrupt(
            session_id="sess-rc07",
            thread_id="th-rc07f",
            tool_name="execute_skill_script",
            confirmation={
                "status": "needs_confirmation",
                "pending": self.pending,
            },
        )
        commission = TaskCommission(
            task_id="tid-rc07-snap",
            zone_id="execute",
            playbook_id="",
            primary_skills=frozenset({"loca-stude"}),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=True,
            channel="heavy",
            max_tool_steps=8,
        )
        task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="写库",
            commission=commission,
            lease_owner="w7",
        )
        # 把审批绑到本任务
        ApprovalRequest.objects.filter(
            interrupt_id=interrupt["interrupt_id"]
        ).update(task=task)
        park_for_approval(task, interrupt_id=interrupt["interrupt_id"])
        snap = task_snapshot(task)
        self.assertEqual(snap["status"], "awaiting_approval")
        self.assertIsNotNone(snap.get("pending_approval"))
        self.assertEqual(
            snap["pending_approval"]["interrupt_id"], interrupt["interrupt_id"]
        )
        self.assertIn("add_testcase", snap["pending_approval"]["original_action"])
        self.assertIn("deploy", snap)
