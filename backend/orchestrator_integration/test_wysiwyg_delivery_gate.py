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
