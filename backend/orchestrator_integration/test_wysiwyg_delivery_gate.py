"""所见所得交付门禁：禁止非传图加购交差。"""
from django.test import SimpleTestCase

from orchestrator_integration.execution_policy import evaluate_delivery_evidence
from orchestrator_integration.stage_plan import infer_auth_scope


class WysiwygDeliveryGateTests(SimpleTestCase):
    def test_infer_requires_wysiwyg_from_goal(self):
        scope = infer_auth_scope(
            "帮我去 tip-1105 分支 CALLIE-us 下一单所见即所得商品，加购即可"
        )
        self.assertTrue(scope.get("requires_wysiwyg_upload"))
        self.assertIn("upload", scope.get("allow_actions") or [])

    def test_reject_name_ring_substitute_after_upload_fail(self):
        scope = infer_auth_scope("传图所见即所得商品加购，不要支付")
        text = (
            "已完成事项\n"
            "传图类所见所得商品未能通过：Please upload your photo，计数器始终停在 0/1，upload.svg\n"
            "改用 Personalized Sterling Silver Name Stacking Rings，Number of Names=2，Ring Size=7\n"
            "cart_id = 53 加购成功，任务完成\n"
        )
        r = evaluate_delivery_evidence(
            tool_results=[text],
            auth_scope=scope,
            target_url="https://callie-us-test-ai-model-tip-1105-web.a.callie.cc/",
        )
        self.assertFalse(r["ok"])
        codes = [b.get("blocker_code") for b in r.get("blockers") or []]
        self.assertTrue(
            {"wysiwyg_goal_substituted", "wysiwyg_upload_required"} & set(codes),
            codes,
        )

    def test_composite_browser_stage_drops_kb_tool(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            stage_mount_policy,
        )

        text = "帮我去 tip-1105 分支 CALLIE 站点下一单所见即所得商品"
        d = classify_user_intent(text)
        stages = build_stage_plan(text, d, playbook_id="browser_act")
        ids = [s.stage_id for s in stages]
        self.assertIn("kb_research", ids)
        self.assertIn("browser_interact", ids)
        br = stage_mount_policy(stages, "browser_interact")
        self.assertNotIn("knowledge_search", br["builtin_tools"])
        self.assertIn("playwright-skill", br["skill_names"])

    def test_reject_deferred_self_heal_and_premature_checkout_ask(self):
        scope = infer_auth_scope("所见即所得传图商品加购即可，不要支付")
        text = (
            "5 点击 Add To Cart 未发生：Please upload your photo，无任何 cart 相关 POST，"
            "导航栏 Cart 仍为 0。\n"
            "五、下一步 (我这边可直接续跑，无需你重写指令)\n"
            "重新进入同一 PDP，先填 Size/文本，再传图，直接点 Add To Cart。\n"
            "是否继续到「提交订单」需要你先确认；同时请提供白名单测试邮箱。\n"
        )
        r = evaluate_delivery_evidence(tool_results=[text], auth_scope=scope)
        self.assertFalse(r["ok"])
        codes = {b.get("blocker_code") for b in r.get("blockers") or []}
        self.assertIn("deferred_self_heal", codes)
        self.assertIn("premature_checkout_ask", codes)

    def test_browser_playbook_requires_same_turn_heal(self):
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import playbook_hint, resolve_playbook
        from orchestrator_integration.skill_zones import resolve_skill_zone

        text = "帮我去 tip-1105 所见即所得传图加购"
        d = classify_user_intent(text)
        p = decide_kb_access_policy(text, d)
        zone = resolve_skill_zone(text, d, p)
        pb = resolve_playbook(text, zone, d, p)
        self.assertIsNotNone(pb)
        hint = playbook_hint(pb)
        self.assertIn("同轮", hint)
        self.assertIn("禁止用「下一步/可直接续跑」", hint)
        self.assertIn("步数纪律", hint)
        self.assertIn("入口已知仍扫 ui-automation", hint)