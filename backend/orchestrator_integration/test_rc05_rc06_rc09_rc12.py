"""RC05 预算硬上限 / RC06 业务与工具计数分离 / RC09 事件序 / RC12 配置冲突。"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import (
    advance_task_stage,
    append_event,
    apply_acceptance,
    consume_budget,
    create_or_resume_task,
    record_step_result,
    task_snapshot,
)
from orchestrator_integration.task_commission import TaskCommission
from orchestrator_integration.task_execution_config import (
    apply_task_config,
    validate_config_overrides,
)


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


class Rc05BudgetHardCapTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc05u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc05", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="加购",
            commission=_commission("tid-rc05", max_steps=3),
            auth_scope={"allow_actions": ["add_to_cart"]},
            stage_plan=[{"stage_id": "browser_interact", "goal": "x"}],
            lease_owner="w5",
        )
        # 强制上限 3（覆盖默认模板）
        b = dict(self.task.budget or {})
        b["max_tool_actions"] = 3
        b["max_tool_steps"] = 3
        self.task.budget = b
        self.task.save(update_fields=["budget"])

    def test_five_actions_only_three_consume(self):
        started = 0
        skipped = 0
        for i in range(5):
            ok, msg = consume_budget(self.task, tool_steps=1)
            if ok:
                started += 1
            else:
                skipped += 1
                self.assertIn("budget_tool", msg)
        self.assertEqual(started, 3)
        self.assertEqual(skipped, 2)
        self.task.refresh_from_db()
        used = int(
            (self.task.budget or {}).get("used_tool_actions")
            or (self.task.budget or {}).get("used_tool_steps")
            or 0
        )
        # 未启动项不记实耗：used 停在 3，不是 5
        self.assertEqual(used, 3)


class Rc06BusinessVsToolTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc06u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc06", title="t"
        )
        stages = [
            {
                "stage_id": "s1",
                "goal": "观察",
                "tool_capabilities": ["read"],
                "depends_on": [],
                "acceptance": "页面可读",
                "auth_actions": [],
            },
            {
                "stage_id": "s2",
                "goal": "加购",
                "tool_capabilities": ["browser"],
                "depends_on": ["s1"],
                "acceptance": "cart_id",
                "auth_actions": ["add_to_cart"],
            },
        ]
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="复合",
            commission=_commission("tid-rc06", max_steps=30),
            stage_plan=stages,
            lease_owner="w6",
        )
        b = dict(self.task.budget or {})
        b["expected_business_steps"] = 6
        b["completed_business_steps"] = 0
        b["max_tool_actions"] = 30
        self.task.budget = b
        self.task.current_stage = "s1"
        self.task.save(update_fields=["budget", "current_stage"])

    def test_observations_do_not_fake_business_progress(self):
        for i in range(4):
            consume_budget(self.task, tool_steps=1)
            record_step_result(
                self.task,
                tool_name="observe",
                tool_input={"n": i},
                structured={"status": "succeeded", "summary": f"read-{i}"},
                summary=f"read-{i}",
                stage_id="s1",
            )
        # 仅观察、验收未过 → 不推进业务完成数
        adv = advance_task_stage(
            self.task, tool_results=["只读观察页面文字，无 cart_id"]
        )
        self.assertFalse(adv.get("advanced"))
        self.task.refresh_from_db()
        self.assertEqual(
            int((self.task.budget or {}).get("completed_business_steps") or 0), 0
        )
        used = int((self.task.budget or {}).get("used_tool_actions") or 0)
        self.assertEqual(used, 4)
        self.assertEqual(
            int((self.task.budget or {}).get("expected_business_steps") or 0), 6
        )


class Rc09EventSequenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc09u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc09", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="事件",
            commission=_commission("tid-rc09"),
            lease_owner="w9",
        )

    def test_monotonic_sequence_and_late_ignored(self):
        seqs_new = []
        for i in range(12):
            evt = append_event(
                self.task, "step_result", status="running", payload={"i": i}
            )
            self.assertTrue(evt.get("occurred_at"))
            seqs_new.append(int(evt.get("sequence") or 0))
        self.assertEqual(seqs_new, sorted(seqs_new))
        self.assertEqual(len(set(seqs_new)), len(seqs_new))

        snap = task_snapshot(self.task, limit=50)
        seqs = [e.get("sequence") for e in snap.get("events") or []]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

        # 完成后迟到失败不得覆盖
        done = apply_acceptance(self.task, {"ok": True, "reasons": []})
        self.assertFalse(done.get("ignored"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
        late = apply_acceptance(
            self.task, {"ok": False, "reasons": ["late_error"]}
        )
        self.assertTrue(late.get("ignored"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")


class Rc12ConfigConflictTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc12u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc12", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="配置",
            commission=_commission("tid-rc12", max_steps=10),
            lease_owner="w12",
        )
        consume_budget(self.task, tool_steps=2)

    def test_validate_rejects_negative_and_unknown(self):
        bad = validate_config_overrides({"max_tool_actions": -3})
        self.assertFalse(bad["ok"])
        unk = validate_config_overrides({"mystery_skill_power": 1})
        self.assertFalse(unk["ok"])

    def test_patch_conflict_and_preserve_usage(self):
        used_before = int((self.task.budget or {}).get("used_tool_actions") or 0)
        ver = int((self.task.budget or {}).get("config_version") or 1)
        ok = apply_task_config(
            self.task,
            expected_version=ver,
            overrides={"max_tool_actions": 20},
            reason="raise cap",
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["config_version"], ver + 1)
        self.task.refresh_from_db()
        self.assertEqual(
            int((self.task.budget or {}).get("used_tool_actions") or 0), used_before
        )
        self.assertEqual(int((self.task.budget or {}).get("max_tool_actions") or 0), 20)

        conflict = apply_task_config(
            self.task,
            expected_version=ver,  # stale
            overrides={"max_tool_actions": 15},
        )
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error_code"], "version_conflict")
        self.assertEqual(conflict["http_status"], 409)

    def test_terminal_task_rejects_config_change(self):
        apply_acceptance(self.task, {"ok": True, "reasons": []})
        self.task.refresh_from_db()
        ver = int((self.task.budget or {}).get("config_version") or 1)
        denied = apply_task_config(
            self.task,
            expected_version=ver,
            overrides={"max_tool_actions": 8},
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error_code"], "task_terminal")
