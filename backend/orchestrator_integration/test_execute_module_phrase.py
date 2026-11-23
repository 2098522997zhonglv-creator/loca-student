"""执行「项目/模块/需求标题」夹在中间的用例话术必须进 execute 区。"""

from __future__ import annotations

import unittest

from orchestrator_integration.intent_router import (
    IntentDecision,
    decide_kb_access_policy,
    is_testcase_execute_intent,
    looks_like_testcase_execute_keywords,
)
from orchestrator_integration.skill_zones import ZONE_EXECUTE, resolve_skill_zone
from orchestrator_integration.task_commission import commission_task
from orchestrator_integration.task_orchestrator import (
    apply_orchestrator_mount,
    build_task_plan,
)

# 用户真实话术：执行与用例之间夹了长标题
_USER_MSG = (
    '执行项目1中“定制器-矢量蒙版支持在画布上调整位置”（模块27）的全部用例'
)


class ExecuteModulePhraseTests(unittest.TestCase):
    def test_keywords_match_long_title(self):
        self.assertTrue(looks_like_testcase_execute_keywords(_USER_MSG))
        self.assertTrue(
            is_testcase_execute_intent(
                _USER_MSG, IntentDecision(intents={"qa"}, primary="qa", reason="t")
            )
        )

    def test_zone_execute_and_nonempty_primary(self):
        d = IntentDecision(intents={"qa"}, primary="qa", reason="t")
        kb = decide_kb_access_policy(_USER_MSG, d)
        self.assertEqual(kb.reason, "testcase_execute")
        self.assertFalse(kb.allow_knowledge_search)
        z = resolve_skill_zone(_USER_MSG, d, kb)
        self.assertEqual(z.zone_id, ZONE_EXECUTE)
        self.assertTrue(z.allow_execute)
        c = commission_task(
            message=_USER_MSG, zone=z, playbook=None, decision=d, kb_policy=kb
        )
        self.assertTrue(c.primary_skills, msg=c.primary_skills)
        self.assertIn("ui-automation", c.primary_skills)
        self.assertIn("loca-stude", c.primary_skills)

    def test_orchestrator_plan_is_execute_path(self):
        d = IntentDecision(intents={"qa"}, primary="qa", reason="t")
        plan = build_task_plan(_USER_MSG, d)
        ids = [n.node_id for n in plan.nodes]
        self.assertIn("query_cases", ids)
        self.assertIn("run_or_browser", ids)
        mount = apply_orchestrator_mount(plan)
        self.assertTrue(
            mount.skill_allowlist
            & {"ui-automation", "loca-stude", "api-automation", "playwright-skill"},
            msg=mount.skill_allowlist,
        )
        self.assertIn("execute_skill_script", mount.builtin_allow)


if __name__ == "__main__":
    unittest.main()
