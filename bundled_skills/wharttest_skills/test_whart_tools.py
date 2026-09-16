import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("whart_tools.py")
MODULE_SPEC = importlib.util.spec_from_file_location("whart_tools_under_test", MODULE_PATH)
whart_tools = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC and MODULE_SPEC.loader
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.get = lambda *args, **kwargs: None
    requests_stub.post = lambda *args, **kwargs: None
    requests_stub.patch = lambda *args, **kwargs: None
    requests_stub.delete = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub
MODULE_SPEC.loader.exec_module(whart_tools)


class _DummyResponse:
    status_code = 200
    headers = {}
    text = ""
    content = b""

    def raise_for_status(self):
        return None


class _JsonResponse(_DummyResponse):
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = ""
        self.content = b"{}"

    def json(self):
        return self.payload


class _BinaryResponse(_DummyResponse):
    def __init__(self, payload: bytes, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.text = payload.decode("utf-8", errors="replace")
        self.content = payload

    def iter_content(self, chunk_size=8192):
        yield self.payload


class WhartToolsFileManagementTests(unittest.TestCase):
    @patch.object(whart_tools.requests, "get", return_value=_JsonResponse({"count": 1, "results": [{"id": 8}]}))
    def test_list_files_passes_filters(self, mock_get):
        result = whart_tools.list_files(
            project_id=1,
            page=2,
            page_size=50,
            search="需求",
            status="available",
            extension=".docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ordering="original_name",
        )

        self.assertEqual(result["count"], 1)
        mock_get.assert_called_once_with(
            f"{whart_tools.BASE_URL}/api/projects/1/files/",
            headers=whart_tools.HEADERS,
            params={
                "page": 2,
                "page_size": 50,
                "search": "需求",
                "status": "available",
                "extension": ".docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "ordering": "original_name",
            },
        )

    @patch.object(whart_tools.requests, "post", return_value=_JsonResponse({"file_id": 8, "name": "需求.txt"}, 201))
    def test_upload_file_uses_multipart_files_field(self, mock_post):
        with tempfile.TemporaryDirectory() as temp_root:
            file_path = os.path.join(temp_root, "需求.txt")
            with open(file_path, "wb") as f:
                f.write(b"# doc")

            result = whart_tools.upload_files(1, file_path)

        self.assertEqual(result["file_id"], 8)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f"{whart_tools.BASE_URL}/api/projects/1/files/")
        self.assertEqual(kwargs["headers"], whart_tools.HEADERS)
        self.assertEqual(kwargs["files"][0][0], "files")
        self.assertEqual(kwargs["files"][0][1][0], "需求.txt")
        self.assertEqual(kwargs["files"][0][1][2], "text/plain")

    @patch.object(whart_tools.requests, "post", return_value=_JsonResponse({"valid": True, "files": []}))
    def test_validate_files_parses_csv_ids(self, mock_post):
        result = whart_tools.validate_files(1, "8, 9,8")

        self.assertEqual(result, {"valid": True, "files": []})
        mock_post.assert_called_once_with(
            f"{whart_tools.BASE_URL}/api/projects/1/files/validate/",
            headers=whart_tools.HEADERS,
            json={"file_ids": [8, 9]},
        )

    @patch.object(
        whart_tools.requests,
        "post",
        return_value=_JsonResponse({"auto_delete_on_unbind": True, "auto_delete_zero_refs": False}),
    )
    def test_update_file_settings_parses_bool_values(self, mock_post):
        result = whart_tools.update_file_settings(1, "true", "false")

        self.assertTrue(result["auto_delete_on_unbind"])
        self.assertFalse(result["auto_delete_zero_refs"])
        mock_post.assert_called_once_with(
            f"{whart_tools.BASE_URL}/api/projects/1/files/settings/",
            headers=whart_tools.HEADERS,
            json={"auto_delete_on_unbind": True, "auto_delete_zero_refs": False},
        )

    @patch.object(whart_tools.requests, "delete")
    def test_delete_file_handles_204(self, mock_delete):
        mock_delete.return_value = _JsonResponse({}, 204)

        result = whart_tools.delete_file(1, 8)

        self.assertTrue(result["success"])
        mock_delete.assert_called_once_with(
            f"{whart_tools.BASE_URL}/api/projects/1/files/8/",
            headers=whart_tools.HEADERS,
        )

    @patch.object(whart_tools.requests, "get")
    def test_download_file_saves_response_content(self, mock_get):
        mock_get.return_value = _BinaryResponse(
            b"hello",
            {
                "Content-Type": "text/plain",
                "Content-Disposition": 'attachment; filename="hello.txt"',
            },
        )
        with tempfile.TemporaryDirectory() as temp_root:
            result = whart_tools.download_file(1, 8, output_dir=temp_root)
            saved_path = result["output_path"]
            with open(saved_path, "rb") as f:
                content = f.read()

        self.assertEqual(content, b"hello")
        self.assertTrue(saved_path.endswith("hello.txt"))
        mock_get.assert_called_once_with(
            f"{whart_tools.BASE_URL}/api/projects/1/files/8/download/",
            headers=whart_tools.HEADERS,
            stream=True,
        )


if __name__ == "__main__":
    unittest.main()
