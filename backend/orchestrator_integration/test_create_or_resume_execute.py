"""create_or_resume：含「继续」的执行用例必须新建，不能消歧/续跑旧任务。"""

from __future__ import annotations

import unittest

from orchestrator_integration.turn_context import (
    TURN_NONE,
    TURN_NEW,
    _is_bare_continue_phrase,
    _looks_like_new_execute_command,
)


class CreateOrResumeExecuteDecisionTests(unittest.TestCase):
    """无 Django：锁定与 create_or_resume_task 相同的判定函数。"""

    def test_user_phrase_is_new_execute_not_bare_continue(self):
        msg = "执行 skill 继续推进执行 SKU素材同步 模块用例"
        self.assertTrue(_looks_like_new_execute_command(msg))
        self.assertFalse(_is_bare_continue_phrase(msg))

    def test_force_new_when_relation_none(self):
        msg = "执行 skill 继续推进执行 SKU素材同步 模块用例"
        new_execute = _looks_like_new_execute_command(msg)
        bare = _is_bare_continue_phrase(msg)
        relation = TURN_NONE
        force_new = (not False) and (
            new_execute or relation in {TURN_NEW, TURN_NONE}
        )
        self.assertTrue(force_new)
        # 模拟 create_or_resume 分支：force_new 时不得走 open_count>=2 消歧
        open_count = 6
        would_ambiguous = (not force_new) and open_count >= 2 and bare
        self.assertFalse(would_ambiguous)
        should_resume = (not force_new) and bare
        self.assertFalse(should_resume)

    def test_bare_continue_still_ambiguous_with_two_open(self):
        msg = "继续"
        self.assertTrue(_is_bare_continue_phrase(msg))
        self.assertFalse(_looks_like_new_execute_command(msg))
        force_new = False
        open_count = 2
        would_ambiguous = (not force_new) and open_count >= 2 and _is_bare_continue_phrase(
            msg
        )
        self.assertTrue(would_ambiguous)

    def test_relation_new_forces_new_without_execute_keywords(self):
        msg = "随便说说继续两句"
        relation = TURN_NEW
        force_new = _looks_like_new_execute_command(msg) or relation in {
            TURN_NEW,
            TURN_NONE,
        }
        self.assertTrue(force_new)


if __name__ == "__main__":
    unittest.main()
