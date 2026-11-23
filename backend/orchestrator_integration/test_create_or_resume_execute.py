"""create_or_resume：含「继续」的执行用例必须新建，不能消歧/续跑旧任务。"""

from __future__ import annotations

import unittest

from orchestrator_integration.runtime.resume_policy import ResumeAction, ResumePolicy
from orchestrator_integration.turn_context import (
    TURN_NONE,
    TURN_NEW,
    is_bare_continue_phrase,
    looks_like_new_execute_command,
)


class CreateOrResumeExecuteDecisionTests(unittest.TestCase):
    """无 Django：经 ResumePolicy 锁定与线上一致的判定。"""

    def test_user_phrase_is_new_execute_not_bare_continue(self):
        msg = "执行 skill 继续推进执行 SKU素材同步 模块用例"
        self.assertTrue(looks_like_new_execute_command(msg))
        self.assertFalse(is_bare_continue_phrase(msg))

    def test_force_new_when_relation_none(self):
        msg = "执行 skill 继续推进执行 SKU素材同步 模块用例"
        d = ResumePolicy.decide(
            goal=msg,
            turn_relation=TURN_NONE,
            open_count=6,
            has_existing=True,
        )
        self.assertEqual(d.action, ResumeAction.CREATE_NEW)

    def test_bare_continue_still_ambiguous_with_two_open(self):
        msg = "继续"
        self.assertTrue(is_bare_continue_phrase(msg))
        self.assertFalse(looks_like_new_execute_command(msg))
        d = ResumePolicy.decide(
            goal=msg, turn_relation="", open_count=2, has_existing=True
        )
        self.assertEqual(d.action, ResumeAction.AMBIGUOUS)

    def test_relation_new_forces_new_without_execute_keywords(self):
        msg = "随便说说继续两句"
        d = ResumePolicy.decide(
            goal=msg, turn_relation=TURN_NEW, open_count=3, has_existing=True
        )
        self.assertEqual(d.action, ResumeAction.CREATE_NEW)


if __name__ == "__main__":
    unittest.main()
