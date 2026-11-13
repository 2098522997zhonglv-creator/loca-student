"""RC17：时效、资源与目标一致性 — 注入负例不得当完成证据。"""
from django.test import SimpleTestCase, TestCase

from orchestrator_integration.execution_policy import evaluate_delivery_evidence
from orchestrator_integration.evidence_integrity import evaluate_evidence_integrity_text
from orchestrator_integration.agent_runtime import probe_browser_resources


class Rc17DeliveryInjectionTests(SimpleTestCase):
    def test_doc_sample_id_rejected(self):
        r = evaluate_delivery_evidence(
            tool_results=[
                "文档示例 cart_id=sample_cart_001；交付结果 cart_id=sample_cart_001 加购成功"
            ],
        )
        self.assertFalse(r["ok"])
        self.assertTrue(
            any(b.get("blocker_code") == "kb_sample_typed_id" for b in r["blockers"])
        )

    def test_missing_asset_claimed_upload(self):
        r = evaluate_delivery_evidence(
            tool_results=["素材文件缺失不可读；上传成功 0/1→1/1"],
        )
        self.assertFalse(r["ok"])
        self.assertTrue(
            any(
                b.get("blocker_code") == "missing_asset_claimed_upload"
                for b in r["blockers"]
            )
        )

    def test_stale_cookie_without_probe(self):
        r = evaluate_delivery_evidence(
            tool_results=["旧 cookie 仍在，会话仍有效，可直接继续加购"],
        )
        self.assertFalse(r["ok"])
        self.assertTrue(
            any(b.get("blocker_code") == "stale_cookie_as_fact" for b in r["blockers"])
        )

    def test_other_sku_preview_not_completion(self):
        r = evaluate_delivery_evidence(
            tool_results=["别的 SKU 预览已刷新；加购成功任务完成"],
        )
        self.assertFalse(r["ok"])
        self.assertTrue(
            any(b.get("blocker_code") == "other_sku_preview" for b in r["blockers"])
        )

    def test_category_experience_hard_when_claimed_ready(self):
        text = (
            "手提袋这类商品已启用普通模式与打印区域，因此可直接上传并加购成功"
        )
        integ = evaluate_evidence_integrity_text(text)
        self.assertFalse(integ["hard_ok"])
        r = evaluate_delivery_evidence(tool_results=[text])
        self.assertFalse(r["ok"])
        self.assertTrue(
            any(
                b.get("blocker_code") == "category_experience_as_capability"
                for b in r["blockers"]
            )
        )

    def test_probe_includes_time_env_url_login_hash_fields(self):
        probe = probe_browser_resources(
            session_id="sess-rc17-missing",
            file_path="/tmp/no_such_rc17_asset.png",
            target_url="https://example.invalid/product/sku-rc17",
        )
        self.assertTrue(probe.get("observed_at"))
        self.assertIn("hostname", probe.get("env") or {})
        self.assertEqual(probe.get("target_url"), "https://example.invalid/product/sku-rc17")
        file_info = (probe.get("probe_result") or {}).get("file") or {}
        self.assertFalse(file_info.get("accessible"))
        # content_version 空字符串表示无哈希可读
        self.assertIn("content_version", file_info)
        sess = (probe.get("probe_result") or {}).get("session") or {}
        self.assertIn("login_state", sess)
        self.assertFalse(probe.get("ok_to_reuse"))


class Rc17PostConditionFactsWireTests(TestCase):
    def test_post_condition_honors_stale_facts(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        verdict = evaluate_post_condition(
            post_condition="加购完成",
            tool_results=[
                'status=succeeded cart_id=oldcart99 source_url=https://x/p/1'
            ],
            playbook_id="browser_act",
            target_url="https://x/p/1",
            facts={"stale_typed_ids": ["oldcart99"], "evidence_epoch": 3},
            evidence_epoch=3,
            stage_plan=[{"stage_id": "browser_interact"}],
        )
        self.assertFalse(verdict.get("ok"))
        delivery = verdict.get("delivery") or {}
        self.assertTrue(
            any(b.get("blocker_code") == "stale_typed_id" for b in delivery.get("blockers") or [])
        )
        self.assertEqual(delivery.get("evidence_epoch"), 3)
