"""RC03 / RC14 / RC15：有界自愈、动作授权、预算/扩权分离。"""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.action_auth import (
    check_auth_actions,
    confirm_capability_expand,
    expand_budget_only,
    record_heal_switch,
    request_capability_expand,
    resolve_equivalent_executor,
)
from orchestrator_integration.agent_runtime import create_or_resume_task
from orchestrator_integration.task_commission import TaskCommission


class ActionAuthUnitTests(SimpleTestCase):
    def test_rc14_deny_pay_before_side_effect(self):
        v = check_auth_actions(
            command="await page.click('text=提交订单'); await pay()",
            skill_name="playwright-skill",
            auth_scope={
                "allow_actions": ["add_to_cart", "upload"],
                "deny_actions": ["pay", "submit_order"],
            },
        )
        self.assertFalse(v["ok"])
        self.assertEqual(v["error_code"], "action_denied")

    def test_rc14_allow_add_to_cart(self):
        v = check_auth_actions(
            command="await addToCart(); // 加购",
            skill_name="playwright-skill",
            auth_scope={"allow_actions": ["add_to_cart"], "deny_actions": ["pay"]},
        )
        self.assertTrue(v["ok"])
        self.assertIn("add_to_cart", v["actions"])

    def test_rc14_skill_available_not_enough_for_extra_action(self):
        v = check_auth_actions(
            command="submit_order now",
            skill_name="playwright-skill",
            auth_scope={"allow_actions": ["add_to_cart"], "deny_actions": []},
        )
        self.assertFalse(v["ok"])
        self.assertEqual(v["error_code"], "action_not_allowed")

    def test_rc03_equivalent_executor_switch(self):
        ok = resolve_equivalent_executor(
            current="playwright-skill",
            candidates=["playwright-cli", "browser-use"],
            allowed_skills={"playwright-skill", "playwright-cli"},
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["to"], "playwright-cli")

        denied = resolve_equivalent_executor(
            current="playwright-skill",
            candidates=["browser-use"],
            allowed_skills={"playwright-skill"},
        )
        self.assertFalse(denied["ok"])


class BudgetExpandTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc15u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc15", title="t"
        )
        self.task = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="加购不要支付",
            commission=TaskCommission(
                task_id="tid-rc15",
                zone_id="browser",
                playbook_id="browser_act",
                primary_skills=frozenset({"playwright-skill"}),
                auxiliary_skills=frozenset(),
                short_circuit=True,
                draft_writes=False,
                channel="light",
                max_tool_steps=5,
            ),
            auth_scope={
                "allow_actions": ["add_to_cart"],
                "deny_actions": ["pay", "submit_order"],
            },
            stage_plan=[{"stage_id": "browser_interact", "goal": "x"}],
            lease_owner="w15",
        )

    def test_rc15_budget_expand_does_not_touch_deny(self):
        before_deny = list(self.task.auth_scope.get("deny_actions") or [])
        before_ver = int((self.task.budget or {}).get("config_version") or 1)
        r = expand_budget_only(self.task, add_tool_actions=10, reason="quota")
        self.assertTrue(r["ok"])
        self.task.refresh_from_db()
        self.assertEqual(
            list(self.task.auth_scope.get("deny_actions") or []), before_deny
        )
        self.assertEqual(
            int(self.task.budget.get("config_version") or 0), before_ver + 1
        )
        self.assertGreater(self.task.budget.get("max_tool_actions"), 5)

    def test_rc15_capability_needs_confirm(self):
        pend = request_capability_expand(
            self.task, allow_actions=["submit_order"], reason="user asked"
        )
        self.assertTrue(pend["ok"])
        self.assertEqual(pend["status"], "pending_confirmation")
        # 未确认前 deny 仍在、allow 无 submit_order
        self.task.refresh_from_db()
        self.assertNotIn(
            "submit_order", self.task.auth_scope.get("allow_actions") or []
        )
        confirmed = confirm_capability_expand(self.task, approve=True)
        self.assertTrue(confirmed["ok"])
        self.task.refresh_from_db()
        # deny 优先：confirm 时若仍在 deny 则不会加入 allow
        # 本实现：confirm 跳过 deny 中的动作
        self.assertNotIn(
            "submit_order", self.task.auth_scope.get("allow_actions") or []
        )
        # 扩一个不在 deny 的动作
        request_capability_expand(
            self.task, allow_actions=["upload"], reason="need upload"
        )
        confirm_capability_expand(self.task, approve=True)
        self.task.refresh_from_db()
        self.assertIn("upload", self.task.auth_scope.get("allow_actions") or [])
        self.assertIn("pay", self.task.auth_scope.get("deny_actions") or [])

    def test_rc03_heal_switch_counted(self):
        rec = record_heal_switch(
            self.task,
            from_skill="playwright-skill",
            to_skill="playwright-cli",
            reason="timeout",
        )
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["count"], 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.facts.get("heal_switch_count"), 1)
