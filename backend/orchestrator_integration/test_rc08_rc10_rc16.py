"""RC08 / RC10 / RC16：续租释放、取消不复活、证据存取。"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import (
    apply_acceptance,
    create_or_resume_task,
    finalize_cancel,
    record_step_result,
    release_lease,
    request_cancel,
)
from orchestrator_integration.evidence_store import read_evidence, store_tool_output_evidence
from orchestrator_integration.models import EvidenceRecord
from orchestrator_integration.task_commission import TaskCommission


class Rc08Rc10Rc16Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc16u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc16", title="t"
        )
        self.commission = TaskCommission(
            task_id="tid-rc16",
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
            max_tool_steps=8,
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="加购",
            commission=self.commission,
            auth_scope={"allow_actions": ["add_to_cart"]},
            stage_plan=[{"stage_id": "browser_interact", "goal": "x"}],
            lease_owner="w-rc",
        )

    def test_rc16_store_and_authorized_read(self):
        stored = store_tool_output_evidence(
            self.task,
            tool_name="browser_click",
            summary="clicked cart",
            full_text="full output cart_id=abc123 " + ("x" * 100),
            structured={"status": "succeeded", "resource_id": "abc123"},
        )
        self.assertTrue(stored["ok"])
        self.assertTrue(
            EvidenceRecord.objects.filter(pk=stored["evidence_id"]).exists()
        )
        page = read_evidence(
            evidence_id=stored["evidence_id"],
            user_id=self.user.id,
            offset=0,
            limit=50,
        )
        self.assertTrue(page["ok"])
        self.assertIn("cart_id", page["chunk"])
        self.assertEqual(page["output_ref"], stored["output_ref"])

        denied = read_evidence(
            evidence_id=stored["evidence_id"],
            user_id=self.user.id + 999,
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied.get("status"), 403)

    def test_rc16_record_step_writes_evidence(self):
        step = record_step_result(
            self.task,
            tool_name="ui_click",
            tool_input={"sel": "#cart"},
            structured={
                "status": "succeeded",
                "summary": "ok",
                "data": {"raw_text": "body with cart_id=z9"},
                "operation_id": "op-rc16-1",
            },
            summary="ok",
            stage_id="browser_interact",
        )
        self.assertIsNotNone(step)
        evid = step.evidence or {}
        self.assertTrue(evid.get("output_ref", "").startswith("evidence:"))
        self.assertTrue(EvidenceRecord.objects.filter(task=self.task).exists())

    def test_rc10_late_step_and_acceptance_ignored_after_cancel(self):
        request_cancel(self.task)
        finalize_cancel(self.task, force=True)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "cancelled")

        late = record_step_result(
            self.task,
            tool_name="late_tool",
            tool_input={},
            structured={"status": "succeeded", "summary": "late"},
            summary="late",
        )
        self.assertIsNone(late)

        ignored = apply_acceptance(self.task, {"ok": True, "reasons": []})
        self.assertTrue(ignored.get("ignored"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "cancelled")

    def test_rc08_release_clears_owner(self):
        self.assertEqual(self.task.lease_owner, "w-rc")
        release_lease(self.task, "w-rc")
        self.task.refresh_from_db()
        self.assertEqual(self.task.lease_owner, "")
        self.assertIsNone(self.task.lease_expires_at)

    def test_rc19_no_progress_blocks(self):
        budget = dict(self.task.budget or {})
        budget["max_no_progress_repeats"] = 2
        self.task.budget = budget
        self.task.save(update_fields=["budget"])
        payload = {
            "status": "succeeded",
            "summary": "same",
            "data": {"raw_text": "unchanged"},
        }
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured=payload,
            summary="same",
            stage_id="browser_interact",
        )
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured=payload,
            summary="same",
            stage_id="browser_interact",
        )
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured=payload,
            summary="same",
            stage_id="browser_interact",
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "blocked")
        self.assertIn("no_progress", self.task.error_message or "")

    def test_rc19_progress_resets_streak(self):
        budget = dict(self.task.budget or {})
        budget["max_no_progress_repeats"] = 2
        self.task.budget = budget
        self.task.status = "running"
        self.task.save(update_fields=["budget", "status"])
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured={"status": "succeeded", "summary": "v1"},
            summary="v1",
            stage_id="browser_interact",
        )
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured={"status": "succeeded", "summary": "v1"},
            summary="v1",
            stage_id="browser_interact",
        )
        # 页面有新状态 → 指纹变化，清零
        record_step_result(
            self.task,
            tool_name="observe",
            tool_input={"url": "x"},
            structured={"status": "succeeded", "summary": "v2-changed"},
            summary="v2-changed",
            stage_id="browser_interact",
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "running")
        self.assertEqual(int((self.task.facts or {}).get("no_progress_streak") or 0), 0)
