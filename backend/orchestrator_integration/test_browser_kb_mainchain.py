"""浏览器主链：知识库取证 → 页面交互，short_circuit 不得拆 KB。"""
from django.test import SimpleTestCase


class BrowserKbMainchainTests(SimpleTestCase):
    def test_callie_branch_plan_keeps_kb_then_browser(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.playbooks import (
            PLAYBOOK_BROWSER_ACT,
            resolve_playbook,
        )
        from orchestrator_integration.skill_zones import resolve_skill_zone
        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            stage_mount_policy,
        )
        from orchestrator_integration.intent_router import decide_kb_access_policy

        text = (
            "帮我去test-ai-model-tip-1105这个分支的CALLIE-us站点"
            "下一单所见即所得商品"
        )
        d = classify_user_intent(text)
        p = decide_kb_access_policy(text, d)
        z = resolve_skill_zone(text, d, p)
        pb = resolve_playbook(text, z, d, p)
        self.assertEqual(pb.playbook_id, PLAYBOOK_BROWSER_ACT)
        self.assertNotIn("knowledge_search", pb.skip)

        stages = build_stage_plan(text, d, playbook_id=PLAYBOOK_BROWSER_ACT)
        ids = [s.stage_id for s in stages]
        self.assertIn("kb_research", ids)
        self.assertIn("browser_interact", ids)
        self.assertLess(ids.index("kb_research"), ids.index("browser_interact"))

        kb_pol = stage_mount_policy(stages, "kb_research")
        self.assertIn("knowledge_search", kb_pol["builtin_tools"])
        br_pol = stage_mount_policy(stages, "browser_interact")
        self.assertIn("knowledge_search", br_pol["builtin_tools"])
        self.assertIn("playwright-skill", br_pol["skill_names"])

    def test_browser_playbook_steps_require_kb_first(self):
        from orchestrator_integration.playbooks import playbook_hint, resolve_playbook
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.skill_zones import resolve_skill_zone

        text = "在 CALLIE 站点传图后加购，不要支付"
        d = classify_user_intent(text)
        pb = resolve_playbook(
            text,
            resolve_skill_zone(text, d, decide_kb_access_policy(text, d)),
            d,
            decide_kb_access_policy(text, d),
        )
        hint = playbook_hint(pb)
        self.assertIn("knowledge_search", hint)
        self.assertIn("禁止本机扫盘", hint)
        self.assertNotIn("禁止 url-reader / knowledge_search", hint)
