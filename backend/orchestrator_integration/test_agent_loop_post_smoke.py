"""AgentLoopStreamAPIView.post 入口回归：防 UnboundLocalError / 消息规范化。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from orchestrator_integration.chat_activity import normalize_user_message


class NormalizeUserMessageTests(unittest.TestCase):
    def test_tab_and_whitespace(self):
        raw = "执行 skill 继续推进执行\tSKU素材同步 模块用例"
        out = normalize_user_message(raw)
        self.assertEqual(out, "执行 skill 继续推进执行 SKU素材同步 模块用例")
        self.assertNotIn("\t", out)

    def test_literal_backslash_t(self):
        raw = r"执行 skill 继续推进执行\tSKU素材同步 模块用例"
        out = normalize_user_message(raw)
        self.assertNotIn("\\t", out)
        self.assertIn("SKU素材同步", out)

    def test_non_str_passthrough(self):
        self.assertIsNone(normalize_user_message(None))
        self.assertEqual(normalize_user_message(123), 123)


class PostMethodAstGuardTests(unittest.TestCase):
    """post() 内禁止再 import re，否则会遮蔽模块级 re 触发 UnboundLocalError。"""

    def test_post_has_no_local_import_re(self):
        path = Path(__file__).resolve().parent / "agent_loop_view.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = []
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef,)):
                continue
            if node.name != "AgentLoopStreamAPIView":
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "post":
                    continue
                for n in ast.walk(item):
                    if isinstance(n, ast.Import):
                        for alias in n.names:
                            if alias.name == "re":
                                hits.append(n.lineno)
                    if isinstance(n, ast.ImportFrom) and (n.module or "") == "re":
                        hits.append(n.lineno)
        self.assertEqual(hits, [], f"post() must not import re locally; lines={hits}")

    def test_post_uses_normalize_helper(self):
        path = Path(__file__).resolve().parent / "agent_loop_view.py"
        text = path.read_text(encoding="utf-8")
        # 锁定调用点，避免再次内联 re.sub 踩坑
        self.assertIn("normalize_user_message(", text)
        self.assertNotIn(
            're.sub(r"[\\t\\r\\f\\v]+"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
