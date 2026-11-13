"""证据完整性 / 审批恢复闭环单元测试。"""
from django.test import SimpleTestCase


class EvidenceIntegrityTests(SimpleTestCase):
    def test_resume_hint_forbids_user_remount(self):
        from orchestrator_integration.evidence_integrity import RESUME_EXEC_HINT

        self.assertIn("重挂", RESUME_EXEC_HINT)
        self.assertIn("放行工具", RESUME_EXEC_HINT)

    def test_product_capability_inference_is_soft(self):
        from orchestrator_integration.evidence_integrity import (
            evaluate_evidence_integrity_text,
        )

        text = (
            "手提袋这类商品已启用普通模式与打印区域，定制图可用，"
            "知识库记载同类目通常支持上传"
        )
        r = evaluate_evidence_integrity_text(text)
        self.assertTrue(r["hard_ok"])
        self.assertFalse(r["soft_ok"])
        self.assertTrue(any("待验证" in x for x in r["soft_reasons"]))

    def test_historical_502_labeled(self):
        from orchestrator_integration.evidence_integrity import (
            classify_http_failure_freshness,
            evaluate_evidence_integrity_text,
            should_inject_evidence_hint,
        )

        self.assertEqual(
            classify_http_failure_freshness("历史 502，尚未复核"),
            "historical_unverified",
        )
        ok = evaluate_evidence_integrity_text(
            "历史异常，尚未复核：上一轮出现 502；本轮将重新探测后再下结论。"
        )
        self.assertTrue(ok["hard_ok"])
        self.assertFalse(should_inject_evidence_hint(zone_id="kb_qa"))
        self.assertTrue(should_inject_evidence_hint(preserve_exec=True))
        self.assertTrue(
            should_inject_evidence_hint(
                zone_id="browser", allow_actions=["add_to_cart"]
            )
        )
