# -*- coding: utf-8 -*-
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("requirement_tools.py")
MODULE_SPEC = importlib.util.spec_from_file_location("requirement_tools_under_test", MODULE_PATH)
requirement_tools = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(requirement_tools)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _unified(data):
    """模拟 UnifiedResponseRenderer 的外层包装。"""
    return {"status": "success", "code": 200, "message": "操作成功", "data": data}


def _paged(results, count=None):
    return {"count": count if count is not None else len(results),
            "next": None, "previous": None, "results": results}


class ListDocumentsTests(unittest.TestCase):
    @patch("requests.get")
    def test_strips_content_and_passes_project(self, mock_get):
        mock_get.return_value = _Response(_unified(_paged([{
            "id": "doc-uuid", "title": "素材库权限", "status": "review_completed",
            "document_type": "txt", "word_count": 14834, "page_count": 30,
            "modules_count": 8, "uploader_name": "admin",
            "uploaded_at": "2026-09-16T14:19:55Z",
            "content": "x" * 50000,
        }])))

        result = requirement_tools.list_documents(1)

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["project"], 1)
        self.assertEqual(result["total"], 1)
        doc = result["documents"][0]
        self.assertEqual(doc["document_id"], "doc-uuid")
        self.assertEqual(doc["word_count"], 14834)
        # list 接口会带回整份正文，必须丢弃
        self.assertNotIn("content", doc)

    @patch("requests.get")
    def test_drops_none_params(self, mock_get):
        mock_get.return_value = _Response(_unified(_paged([])))
        requirement_tools.list_documents(1)
        _, kwargs = mock_get.call_args
        self.assertNotIn("status", kwargs["params"])
        self.assertNotIn("search", kwargs["params"])

    @patch("requests.get")
    def test_403_returns_actionable_error(self, mock_get):
        mock_get.return_value = _Response({}, status_code=403)
        result = requirement_tools.list_documents(99)
        self.assertIn("403", result["error"])
        self.assertIn("project_id", result["error"])


class GetDocumentTests(unittest.TestCase):
    @patch("requests.get")
    def test_truncates_content_and_reports_full_length(self, mock_get):
        mock_get.return_value = _Response(_unified({
            "id": "doc-uuid", "title": "素材库权限", "content": "字" * 10000,
            "latest_review": {"id": "report-uuid", "status": "completed"},
        }))

        result = requirement_tools.get_document(1, "doc-uuid", max_chars=100)

        self.assertTrue(result["content"].startswith("字" * 100))
        self.assertIn("10000", result["content"])
        self.assertEqual(result["latest_report_id"], "report-uuid")

    @patch("requests.get")
    def test_max_chars_zero_keeps_full_text(self, mock_get):
        mock_get.return_value = _Response(_unified({"id": "d", "content": "字" * 500}))
        result = requirement_tools.get_document(1, "d", max_chars=0)
        self.assertEqual(len(result["content"]), 500)


class ReportResolutionTests(unittest.TestCase):
    @patch("requests.get")
    def test_document_id_resolves_latest_report(self, mock_get):
        mock_get.side_effect = [
            _Response(_unified(_paged([{"id": "report-uuid"}]))),
            _Response(_unified({"id": "report-uuid", "status": "completed",
                                "total_issues": 13, "scores": {"completeness": 72}})),
        ]

        result = requirement_tools.get_report(1, document_id="doc-uuid")

        first_params = mock_get.call_args_list[0][1]["params"]
        self.assertEqual(first_params["document"], "doc-uuid")
        self.assertEqual(first_params["ordering"], "-review_date")
        self.assertEqual(result["report_id"], "report-uuid")
        self.assertEqual(result["total_issues"], 13)

    @patch("requests.get")
    def test_missing_report_surfaces_error(self, mock_get):
        mock_get.return_value = _Response(_unified(_paged([])))
        result = requirement_tools.get_report(1, document_id="doc-uuid")
        self.assertIn("还没有评审报告", result["error"])

    def test_requires_one_of_document_or_report(self):
        result = requirement_tools.get_report(1)
        self.assertIn("--report_id", result["error"])


class ListIssuesTests(unittest.TestCase):
    @patch("requests.get")
    def test_filters_by_priority_and_report(self, mock_get):
        mock_get.return_value = _Response(_unified(_paged([{
            "id": "issue-uuid", "priority": "high", "issue_type": "completeness",
            "title": "缺少异常流程", "module_name": "权限模块", "is_resolved": False,
        }], count=5)))

        result = requirement_tools.list_issues(1, report_id="report-uuid", priority="high")

        params = mock_get.call_args[1]["params"]
        self.assertEqual(params["report"], "report-uuid")
        self.assertEqual(params["priority"], "high")
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["issues"][0]["priority"], "high")


class ModuleResultTests(unittest.TestCase):
    @patch("requests.get")
    def test_truncates_long_analysis(self, mock_get):
        mock_get.return_value = _Response(_unified(_paged([{
            "module_name": "权限模块", "module_rating": "good",
            "issues_count": 3, "severity_score": 40,
            "analysis_content": "分" * 5000,
        }])))

        result = requirement_tools.list_module_results(1, report_id="report-uuid")

        analysis = result["module_results"][0]["analysis"]
        self.assertIn("已截断", analysis)
        self.assertLess(len(analysis), 5000)


class UnwrapTests(unittest.TestCase):
    def test_handles_raw_payload_without_unified_wrapper(self):
        self.assertEqual(requirement_tools._unwrap({"count": 1}), {"count": 1})

    def test_unwraps_unified_payload(self):
        self.assertEqual(requirement_tools._unwrap(_unified({"a": 1})), {"a": 1})


if __name__ == "__main__":
    unittest.main()
