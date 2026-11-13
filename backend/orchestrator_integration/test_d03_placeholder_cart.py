"""D03 / RC18：静态 Preview / 占位 cart_id 必须失败。"""
from django.test import SimpleTestCase


class PlaceholderCartEvidenceTests(SimpleTestCase):
    def test_observed_cart_action_rejected(self):
        from orchestrator_integration.execution_policy import evaluate_browser_checkpoints

        chk = evaluate_browser_checkpoints(
            "Add To Cart ok; preview refreshed; cart_id=observed_cart_action"
        )
        self.assertFalse(chk["ok"])
        self.assertFalse(chk["checks"]["has_typed_identifier"])
        self.assertTrue(any("占位" in r for r in chk["reasons"]))

    def test_static_preview_text_rejected(self):
        from orchestrator_integration.execution_policy import evaluate_browser_checkpoints

        chk = evaluate_browser_checkpoints(
            "页面含 Preview 字样 static preview；仅有 preview 文本；已加入购物车"
        )
        self.assertFalse(chk["ok"])

    def test_real_cart_id_still_passes(self):
        from orchestrator_integration.execution_policy import evaluate_browser_checkpoints

        chk = evaluate_browser_checkpoints(
            "Add To Cart ok; preview refreshed; cart_id=cart_abc123; 素材已关联"
        )
        self.assertTrue(chk["ok"])
        self.assertTrue(chk["checks"]["has_typed_identifier"])

    def test_extract_skips_placeholder_ids(self):
        from orchestrator_integration.tool_result import extract_typed_identifiers

        ids = extract_typed_identifiers(
            "cart_id=observed_cart_action; order_sn=sample"
        )
        self.assertEqual(ids, {})
