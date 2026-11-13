"""D05 / RC04：批次输出引用负例必须在后继启动前拒绝。"""
from django.test import SimpleTestCase


class OutputRefGateTests(SimpleTestCase):
    def test_negative_sources_rejected(self):
        from orchestrator_integration.step_deps import resolve_output_refs

        cmd = "python x.py --id ${a.case_id}"
        cases = [
            ({}, "不存在"),
            ({"a": {"status": "failed", "data": {"case_id": "9"}}}, "failed"),
            ({"a": {"status": "unknown", "data": {"case_id": "9"}}}, "unknown"),
            ({"a": {"data": {"case_id": "9"}}}, "missing_status"),
        ]
        for outputs, label in cases:
            err, _ = resolve_output_refs(cmd, outputs)
            self.assertIsNotNone(err, label)
            self.assertIn("依赖输出缺失或未成功", err or "")

    def test_succeeded_source_resolves(self):
        from orchestrator_integration.step_deps import resolve_output_refs

        err, resolved = resolve_output_refs(
            "python x.py --id ${a.case_id}",
            {"a": {"status": "succeeded", "data": {"case_id": "42"}}},
        )
        self.assertIsNone(err)
        self.assertIn("42", resolved)

    def test_validate_batch_rejects_unknown_ref(self):
        from orchestrator_integration.step_deps import validate_batch_deps

        err = validate_batch_deps(
            [
                {"step_id": "a", "command": "python create.py"},
                {
                    "step_id": "b",
                    "depends_on": ["a"],
                    "command": "python use.py --id ${missing.case_id}",
                },
            ]
        )
        self.assertIsNotNone(err)
        self.assertIn("missing", err or "")
