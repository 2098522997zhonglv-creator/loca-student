"""ResumePolicy 纯决策单测（无 DB）。"""

from __future__ import annotations

import unittest

from orchestrator_integration.runtime.resume_policy import (
    ResumeAction,
    ResumePolicy,
)
from orchestrator_integration.turn_context import TURN_NONE


class ResumePolicyTests(unittest.TestCase):
    def test_execute_continue_phrase_creates_new(self):
        d = ResumePolicy.decide(
            goal="执行 skill 继续推进执行 SKU素材同步 模块用例",
            turn_relation=TURN_NONE,
            open_count=6,
            has_existing=True,
        )
        self.assertEqual(d.action, ResumeAction.CREATE_NEW)
        self.assertIn("force_new", d.reason)

    def test_bare_continue_with_two_open_is_ambiguous(self):
        d = ResumePolicy.decide(
            goal="继续",
            turn_relation="",
            open_count=2,
            has_existing=True,
        )
        self.assertEqual(d.action, ResumeAction.AMBIGUOUS)

    def test_bare_continue_single_open_resumes(self):
        d = ResumePolicy.decide(
            goal="继续",
            turn_relation="",
            open_count=1,
            has_existing=True,
        )
        self.assertEqual(d.action, ResumeAction.RESUME)
        self.assertTrue(d.continue_like)

    def test_terminal_resume_id(self):
        d = ResumePolicy.decide(
            goal="继续",
            resume_task_id="dead",
            has_existing=False,
            terminal_hit=True,
            open_count=0,
        )
        self.assertEqual(d.action, ResumeAction.RETURN_TERMINAL)

    def test_default_create_when_no_existing(self):
        d = ResumePolicy.decide(
            goal="帮我看看日志",
            turn_relation="",
            open_count=0,
            has_existing=False,
        )
        self.assertEqual(d.action, ResumeAction.CREATE_NEW)


if __name__ == "__main__":
    unittest.main()
