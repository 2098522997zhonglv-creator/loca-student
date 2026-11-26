"""短句「继续」不得因僵尸未完成任务而静默结束。"""

from __future__ import annotations

import unittest

from orchestrator_integration.turn_context import (
    TURN_AMBIGUOUS,
    TURN_CONTINUE,
    TURN_NONE,
    TURN_NEW,
    PriorTaskContext,
    classify_turn_relation,
    executable_open_tasks,
    should_preserve_execution,
)


class BareContinuePhraseTests(unittest.TestCase):
    def test_no_open_task_is_none_not_ambiguous(self):
        rel = classify_turn_relation("继续", None, open_count=0)
        self.assertEqual(rel, TURN_NONE)
        self.assertFalse(should_preserve_execution(None, rel))

    def test_two_zombie_tasks_not_ambiguous(self):
        tasks = [
            PriorTaskContext(
                task_pk=1,
                public_task_id="a",
                goal="查知识库",
                current_stage="",
                status="running",
            ),
            PriorTaskContext(
                task_pk=2,
                public_task_id="b",
                goal="再问一句",
                current_stage="kb_qa",
                status="pending",
            ),
        ]
        n = len(executable_open_tasks(tasks))
        self.assertEqual(n, 0)
        self.assertEqual(
            classify_turn_relation("继续", None, open_count=n), TURN_NONE
        )

    def test_two_executable_still_ambiguous(self):
        tasks = [
            PriorTaskContext(
                task_pk=1,
                public_task_id="a",
                goal="跑用例A",
                current_stage="execute",
                status="running",
            ),
            PriorTaskContext(
                task_pk=2,
                public_task_id="b",
                goal="加购B",
                current_stage="browser_interact",
                auth_scope={"allow_actions": ["add_to_cart"]},
                status="running",
            ),
        ]
        execs = executable_open_tasks(tasks)
        self.assertEqual(len(execs), 2)
        self.assertEqual(
            classify_turn_relation("继续", execs[0], open_count=len(execs)),
            TURN_AMBIGUOUS,
        )

    def test_single_executable_continues(self):
        prior = PriorTaskContext(
            task_pk=1,
            public_task_id="a",
            goal="执行模块27",
            current_stage="execute_testcase",
            status="running",
        )
        rel = classify_turn_relation("继续", prior, open_count=1)
        self.assertEqual(rel, TURN_CONTINUE)
        self.assertTrue(should_preserve_execution(prior, rel))

    def test_blocked_fetch_conditions_with_action_plan_is_resumable(self):
        prior = PriorTaskContext(
            task_pk=3,
            public_task_id="blocked-browser-task",
            goal="在测试站完成加购并核对购物车",
            current_stage="fetch_conditions",
            stage_plan=[
                {"stage_id": "fetch_conditions"},
                {"stage_id": "check_executable"},
                {"stage_id": "add_to_cart"},
                {"stage_id": "verify_deliver"},
            ],
            status="blocked",
        )
        resumable = executable_open_tasks([prior])
        self.assertEqual(resumable, [prior])
        rel = classify_turn_relation("继续执行", prior, open_count=len(resumable))
        self.assertEqual(rel, TURN_CONTINUE)
        self.assertTrue(should_preserve_execution(prior, rel))

    def test_blocked_kb_shell_without_action_plan_is_not_resumable(self):
        prior = PriorTaskContext(
            task_pk=4,
            public_task_id="blocked-kb-shell",
            goal="查询商品中心规则",
            current_stage="fetch_conditions",
            stage_plan=[{"stage_id": "fetch_conditions"}],
            status="blocked",
        )
        self.assertEqual(executable_open_tasks([prior]), [])
        self.assertEqual(
            classify_turn_relation("继续执行", prior, open_count=0), TURN_NEW
        )


if __name__ == "__main__":
    unittest.main()
