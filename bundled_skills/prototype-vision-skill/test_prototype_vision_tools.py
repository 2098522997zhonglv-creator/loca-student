# -*- coding: utf-8 -*-
import unittest

from prototype_vision_tools import (
    _gate_ok,
    _make_gate_token,
    _missing_required_sections,
    assemble_content,
    save_document_content,
)


class PrototypeVisionSkillTests(unittest.TestCase):
    def test_gate_ok_requires_hits_or_answer(self):
        self.assertFalse(_gate_ok({}))
        self.assertTrue(_gate_ok({"answer": "有摘要"}))
        self.assertTrue(_gate_ok({"sources": [{"title": "模板"}]}))

    def test_assemble_blocked_without_kb_hits(self):
        result = assemble_content("第1页文字", {"gate_ok": False, "sources": []})
        self.assertIn("error", result)
        self.assertFalse(result.get("format_ok", True))

    def test_assemble_builds_template_with_gate(self):
        kb = {
            "gate_ok": True,
            "gate_token": "abc123",
            "query": "需求模板",
            "answer": "按模块写清交互",
            "sources": [{"title": "原型评审规范"}],
        }
        result = assemble_content("=== 第1页 ===\n按钮：提交", kb, title="加价提醒")
        self.assertTrue(result["gate_ok"])
        self.assertTrue(result["format_ok"])
        self.assertEqual(result["gate_token"], "abc123")
        self.assertIn("## 2. 背景与目标", result["content"])
        self.assertIn("原型评审规范", result["content"])
        self.assertEqual(_missing_required_sections(result["content"]), [])

    def test_save_requires_gate_token(self):
        result = save_document_content(1, "doc-id", "# t\n## 2. 背景与目标\n", "")
        self.assertIn("gate_token", result["error"])

    def test_gate_token_stable(self):
        payload = {"answer": "x", "sources": [{"title": "A"}]}
        a = _make_gate_token("kb1", "q", payload)
        b = _make_gate_token("kb1", "q", payload)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
