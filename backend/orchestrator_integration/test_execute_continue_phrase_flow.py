"""用户真实话术全链路：含「继续」的执行用例 ≠ 续跑旧 fetch_conditions 任务。"""

from __future__ import annotations

import unittest

from orchestrator_integration.chat_activity import normalize_user_message
from orchestrator_integration.intent_router import (
    IntentDecision,
    decide_kb_access_policy,
    is_testcase_execute_intent,
    looks_like_testcase_execute_keywords,
)
from orchestrator_integration.skill_zones import ZONE_EXECUTE, resolve_skill_zone
from orchestrator_integration.task_commission import commission_task
from orchestrator_integration.task_orchestrator import apply_orchestrator_mount, build_task_plan
from orchestrator_integration.turn_context import (
    TURN_CONTINUE,
    TURN_NEW,
    PriorTaskContext,
    classify_turn_relation,
    executable_open_tasks,
    routing_text_for_classify,
    should_preserve_execution,
)

# 与线上一致（含 tab）
_RAW = "执行 skill 继续推进执行\tSKU素材同步 模块用例"


class ExecuteContinuePhraseFlowTests(unittest.TestCase):
    def setUp(self):
        self.msg = normalize_user_message(_RAW)
        self.zombie_prior = PriorTaskContext(
            task_pk=1,
            public_task_id="a9e800b72d85",
            goal="旧的知识库/条件拉取任务",
            current_stage="fetch_conditions",
            status="running",
        )
        self.open_zombies = [
            self.zombie_prior,
            PriorTaskContext(
                task_pk=2,
                public_task_id="z2",
                goal="qa",
                current_stage="",
                status="pending",
            ),
        ]

    def test_normalize_strips_tab(self):
        self.assertNotIn("\t", self.msg)
        self.assertIn("SKU素材同步", self.msg)

    def test_keywords_and_intent(self):
        self.assertTrue(looks_like_testcase_execute_keywords(self.msg))
        # 不依赖 langchain：用关键字路径判定执行意图
        d = IntentDecision(intents={"qa"}, primary="qa", reason="t")
        self.assertTrue(is_testcase_execute_intent(self.msg, d))

    def test_not_continue_old_fetch_conditions(self):
        exec_open = executable_open_tasks(self.open_zombies)
        self.assertEqual(exec_open, [])
        rel = classify_turn_relation(
            self.msg,
            self.zombie_prior,
            open_count=len(exec_open),
        )
        self.assertEqual(rel, TURN_NEW)
        self.assertFalse(should_preserve_execution(self.zombie_prior, rel))
        routed = routing_text_for_classify(self.msg, self.zombie_prior, rel)
        self.assertEqual(routed, self.msg)
        self.assertNotIn("旧的知识库", routed)

    def test_bare_continue_still_continues_executable(self):
        prior = PriorTaskContext(
            task_pk=3,
            public_task_id="e1",
            goal="执行模块27",
            current_stage="execute",
            status="running",
        )
        rel = classify_turn_relation("继续", prior, open_count=1)
        self.assertEqual(rel, TURN_CONTINUE)
        self.assertTrue(should_preserve_execution(prior, rel))

    def test_mount_execute_zone_and_tools(self):
        # 模拟被误标为 qa 的决策；执行关键字仍应进 execute 区
        d = IntentDecision(intents={"qa"}, primary="qa", reason="t")
        kb = decide_kb_access_policy(self.msg, d)
        self.assertEqual(kb.reason, "testcase_execute")
        z = resolve_skill_zone(self.msg, d, kb)
        self.assertEqual(z.zone_id, ZONE_EXECUTE)
        self.assertTrue(z.allow_execute)
        c = commission_task(
            message=self.msg, zone=z, playbook=None, decision=d, kb_policy=kb
        )
        self.assertIn("ui-automation", c.primary_skills)
        self.assertIn("loca-stude", c.primary_skills)
        plan = build_task_plan(self.msg, d)
        ids = [n.node_id for n in plan.nodes]
        self.assertIn("query_cases", ids)
        mount = apply_orchestrator_mount(plan)
        self.assertIn("execute_skill_script", mount.builtin_allow)


if __name__ == "__main__":
    unittest.main()
