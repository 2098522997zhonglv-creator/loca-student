"""RC11：response_locale 推断与英文旁白识别。"""
from django.test import SimpleTestCase


class MessageLocaleTests(SimpleTestCase):
    def test_infer_zh_from_chinese_goal(self):
        from orchestrator_integration.message_locale import infer_response_locale

        self.assertEqual(infer_response_locale("帮我加购这个商品"), "zh")

    def test_infer_en_from_english_goal(self):
        from orchestrator_integration.message_locale import infer_response_locale

        self.assertEqual(
            infer_response_locale("Please add this product to the cart now"),
            "en",
        )

    def test_explicit_override(self):
        from orchestrator_integration.message_locale import infer_response_locale

        self.assertEqual(
            infer_response_locale("帮我加购", explicit="en"), "en"
        )

    def test_english_narration_detection(self):
        from orchestrator_integration.message_locale import (
            looks_like_english_tool_narration,
            should_demote_stream_chunk,
        )

        self.assertTrue(
            looks_like_english_tool_narration(
                "I'll now call the knowledge_search tool to find rules."
            )
        )
        self.assertFalse(looks_like_english_tool_narration("正在检索知识库并整理答案。"))
        self.assertTrue(
            should_demote_stream_chunk(
                locale="zh",
                text="Let me search the knowledge base first.",
            )
        )
        self.assertFalse(
            should_demote_stream_chunk(
                locale="en",
                text="Let me search the knowledge base first.",
            )
        )

    def test_tag_sse_event(self):
        from orchestrator_integration.message_locale import tag_sse_event

        ev = tag_sse_event(
            {"type": "stream", "data": "hi"},
            message_kind="final",
            response_locale="zh",
        )
        self.assertEqual(ev["message_kind"], "final")
        self.assertEqual(ev["response_locale"], "zh")
