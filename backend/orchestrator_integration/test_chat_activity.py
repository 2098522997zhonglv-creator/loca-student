"""chat_active 心跳文件：自动更新杀进程前用来判断是否空闲。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ChatActivityTests(unittest.TestCase):
    def test_touch_and_age(self):
        from orchestrator_integration import chat_activity as ca

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            with mock.patch.object(ca, "_marker_path", return_value=log_dir / "chat_active.ts"):
                ca.touch_chat_active(session_id="s1", reason="t")
                age = ca.seconds_since_chat_active()
                self.assertIsNotNone(age)
                self.assertGreaterEqual(age, 0.0)
                self.assertLess(age, 5.0)
                marker = log_dir / "chat_active.ts"
                self.assertTrue(marker.exists())
                self.assertIn("session=s1", marker.read_text(encoding="utf-8"))

    def test_import_routing_deps_keys(self):
        from orchestrator_integration.chat_activity import import_agent_loop_routing_deps

        # 无 django/langchain 时可能失败；有则校验关键键
        try:
            deps = import_agent_loop_routing_deps()
        except ImportError:
            self.skipTest("runtime deps unavailable")
        for key in (
            "classify_user_intent",
            "list_open_task_contexts",
            "TURN_AMBIGUOUS",
            "get_builtin_tools",
        ):
            self.assertIn(key, deps)


if __name__ == "__main__":
    unittest.main()
