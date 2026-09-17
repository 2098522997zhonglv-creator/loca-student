# -*- coding: utf-8 -*-
"""需求评审数据只读查询工具。

覆盖 /api/requirements/ 下的文档、模块拆分、评审报告与评审问题。
只做查询，不提供上传、发起评审等写操作。
"""

import sys
import io

# Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import argparse
import json
import os
import requests
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_API_KEY = "wharttest-default-mcp-key-2025"

# 正文与长文本字段的默认截断长度，避免一次查询塞爆模型上下文
_DEFAULT_CONTENT_CHARS = 4000
_ANALYSIS_PREVIEW_CHARS = 600


def _base_url() -> str:
    return (os.environ.get("WHARTTEST_BACKEND_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("WHARTTEST_API_KEY") or _DEFAULT_API_KEY).strip()


def _headers() -> dict:
    return {
        "accept": "application/json, text/plain,*/*",
        "X-API-Key": _api_key(),
    }


def _unwrap(payload):
    """剥掉 UnifiedResponseRenderer 的 {status, code, message, data} 外层。"""
    if isinstance(payload, dict) and "data" in payload and "status" in payload:
        return payload.get("data")
    return payload


def _get(path: str, params: dict):
    """发起 GET 请求并返回解包后的数据；失败时返回 {"error": ...}。"""
    url = f"{_base_url()}/api/requirements/{path}"
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = requests.get(url, headers=_headers(), params=clean, timeout=30)
    except Exception as e:
        return {"error": f"请求失败: {e}"}

    if resp.status_code == 403:
        return {"error": "无权访问（403）。请确认 --project_id 正确，且当前 API Key 所属用户是该项目成员。"}
    if resp.status_code == 404:
        return {"error": f"资源不存在（404）: {url}"}
    if resp.status_code >= 400:
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)[:300]
        except Exception:
            detail = (resp.text or "")[:300]
        return {"error": f"HTTP {resp.status_code}: {detail}"}

    try:
        return _unwrap(resp.json())
    except Exception:
        return {"error": "响应不是合法 JSON"}


def _results(data):
    """从分页结构里取出条目数组。"""
    if isinstance(data, dict) and "results" in data:
        return data.get("results") or []
    return data if isinstance(data, list) else []


def _truncate(text, limit):
    """按字符数截断，并标注剩余长度。"""
    if not text:
        return text
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[已截断，全文共 {len(text)} 字符]"


def _resolve_report_id(project_id: int, document_id: str):
    """按文档取最近一次评审报告的 id。"""
    data = _get("reports/", {"project": project_id, "document": document_id,
                             "ordering": "-review_date", "page_size": 1})
    if isinstance(data, dict) and "error" in data:
        return data
    items = _results(data)
    if not items:
        return {"error": f"文档 {document_id} 还没有评审报告"}
    return items[0].get("id")


def list_documents(project_id: int, status: str = None, search: str = None,
                   page: int = 1, page_size: int = 20):
    """列出项目下的需求文档（不含正文）。"""
    data = _get("documents/", {"project": project_id, "status": status,
                               "search": search, "page": page, "page_size": page_size})
    if isinstance(data, dict) and "error" in data:
        return data

    # 接口的 list 会带回整份正文，这里丢掉只留概览字段
    documents = [{
        "document_id": d.get("id"),
        "title": d.get("title"),
        "status": d.get("status"),
        "document_type": d.get("document_type"),
        "word_count": d.get("word_count"),
        "page_count": d.get("page_count"),
        "modules_count": d.get("modules_count"),
        "uploader": d.get("uploader_name"),
        "uploaded_at": d.get("uploaded_at"),
    } for d in _results(data)]

    return {"total": data.get("count") if isinstance(data, dict) else len(documents),
            "documents": documents}


def get_document(project_id: int, document_id: str, max_chars: int = _DEFAULT_CONTENT_CHARS):
    """获取需求文档详情与正文。"""
    data = _get(f"documents/{document_id}/", {"project": project_id})
    if isinstance(data, dict) and "error" in data:
        return data

    latest = data.get("latest_review") or {}
    return {
        "document_id": data.get("id"),
        "title": data.get("title"),
        "description": data.get("description"),
        "status": data.get("status"),
        "document_type": data.get("document_type"),
        "word_count": data.get("word_count"),
        "page_count": data.get("page_count"),
        "modules_count": data.get("modules_count"),
        "uploader": data.get("uploader_name"),
        "uploaded_at": data.get("uploaded_at"),
        "content": _truncate(data.get("content"), max_chars),
        "latest_report_id": latest.get("id"),
        "latest_report_status": latest.get("status"),
    }


def list_modules(project_id: int, document_id: str, page_size: int = 100):
    """列出文档的模块拆分结果。"""
    data = _get("modules/", {"project": project_id, "document": document_id,
                             "page_size": page_size})
    if isinstance(data, dict) and "error" in data:
        return data

    modules = [{
        "module_id": m.get("id"),
        "title": m.get("title"),
        "order": m.get("order"),
        "start_page": m.get("start_page"),
        "end_page": m.get("end_page"),
        "issues_count": m.get("issues_count"),
        "is_auto_generated": m.get("is_auto_generated"),
    } for m in _results(data)]

    return {"total": len(modules), "modules": modules}


def get_report(project_id: int, document_id: str = None, report_id: str = None):
    """获取评审报告概要（不展开问题清单，请用 list_issues）。"""
    if not report_id:
        if not document_id:
            return {"error": "需要提供 --report_id 或 --document_id"}
        report_id = _resolve_report_id(project_id, document_id)
        if isinstance(report_id, dict):
            return report_id

    data = _get(f"reports/{report_id}/", {"project": project_id})
    if isinstance(data, dict) and "error" in data:
        return data

    return {
        "report_id": data.get("id"),
        "document_id": data.get("document"),
        "document_title": data.get("document_title"),
        "status": data.get("status"),
        "progress": data.get("progress"),
        "current_step": data.get("current_step"),
        "review_date": data.get("review_date"),
        "overall_rating": data.get("overall_rating"),
        "completion_score": data.get("completion_score"),
        "scores": data.get("scores"),
        "total_issues": data.get("total_issues"),
        "high_priority_issues": data.get("high_priority_issues"),
        "medium_priority_issues": data.get("medium_priority_issues"),
        "low_priority_issues": data.get("low_priority_issues"),
        "summary": data.get("summary"),
        "recommendations": data.get("recommendations"),
    }


def list_issues(project_id: int, report_id: str = None, document_id: str = None,
                priority: str = None, issue_type: str = None,
                is_resolved: str = None, page: int = 1, page_size: int = 50):
    """列出评审发现的问题。"""
    if not report_id:
        if not document_id:
            return {"error": "需要提供 --report_id 或 --document_id"}
        report_id = _resolve_report_id(project_id, document_id)
        if isinstance(report_id, dict):
            return report_id

    data = _get("issues/", {"project": project_id, "report": report_id,
                            "priority": priority, "issue_type": issue_type,
                            "is_resolved": is_resolved,
                            "page": page, "page_size": page_size})
    if isinstance(data, dict) and "error" in data:
        return data

    issues = [{
        "issue_id": i.get("id"),
        "priority": i.get("priority"),
        "issue_type": i.get("issue_type"),
        "title": i.get("title"),
        "description": i.get("description"),
        "suggestion": i.get("suggestion"),
        "location": i.get("location"),
        "section": i.get("section"),
        "page_number": i.get("page_number"),
        "module_name": i.get("module_name"),
        "is_resolved": i.get("is_resolved"),
    } for i in _results(data)]

    return {"report_id": report_id,
            "total": data.get("count") if isinstance(data, dict) else len(issues),
            "issues": issues}


def list_module_results(project_id: int, report_id: str = None, document_id: str = None,
                        page_size: int = 100):
    """列出各模块的评审结果。"""
    if not report_id:
        if not document_id:
            return {"error": "需要提供 --report_id 或 --document_id"}
        report_id = _resolve_report_id(project_id, document_id)
        if isinstance(report_id, dict):
            return report_id

    data = _get("module-results/", {"project": project_id, "report": report_id,
                                    "page_size": page_size})
    if isinstance(data, dict) and "error" in data:
        return data

    results = [{
        "module_name": r.get("module_name"),
        "module_rating": r.get("module_rating"),
        "issues_count": r.get("issues_count"),
        "severity_score": r.get("severity_score"),
        "analysis": _truncate(r.get("analysis_content"), _ANALYSIS_PREVIEW_CHARS),
        "strengths": r.get("strengths"),
        "weaknesses": r.get("weaknesses"),
        "recommendations": r.get("recommendations"),
    } for r in _results(data)]

    return {"report_id": report_id, "total": len(results), "module_results": results}


ACTIONS = {
    "list_documents": lambda args: list_documents(
        args.project_id, args.status, args.search, args.page, args.page_size
    ),
    "get_document": lambda args: get_document(
        args.project_id, args.document_id, args.max_chars
    ),
    "list_modules": lambda args: list_modules(args.project_id, args.document_id),
    "get_report": lambda args: get_report(
        args.project_id, args.document_id, args.report_id
    ),
    "list_issues": lambda args: list_issues(
        args.project_id, args.report_id, args.document_id, args.priority,
        args.issue_type, args.is_resolved, args.page, args.page_size
    ),
    "list_module_results": lambda args: list_module_results(
        args.project_id, args.report_id, args.document_id
    ),
}


def main():
    parser = argparse.ArgumentParser(description="需求评审数据只读查询工具")
    parser.add_argument("--action", required=True, choices=ACTIONS.keys(), help="要执行的操作")
    parser.add_argument("--project_id", type=int, required=True, help="项目ID（整数，必填）")
    parser.add_argument("--document_id", help="需求文档ID（UUID）")
    parser.add_argument("--report_id", help="评审报告ID（UUID）")
    parser.add_argument("--status", help="文档状态过滤")
    parser.add_argument("--search", help="搜索关键词（标题/描述/正文）")
    parser.add_argument("--priority", help="问题优先级过滤 (high/medium/low)")
    parser.add_argument("--issue_type", help="问题类型过滤")
    parser.add_argument("--is_resolved", help="是否已解决 (true/false)")
    parser.add_argument("--max_chars", type=int, default=_DEFAULT_CONTENT_CHARS,
                        help=f"正文最大返回字符数，0 表示不截断（默认 {_DEFAULT_CONTENT_CHARS}）")
    parser.add_argument("--page", type=int, default=1, help="页码")
    parser.add_argument("--page_size", type=int, default=20, help="每页数量（上限 200）")

    args = parser.parse_args()
    result = ACTIONS[args.action](args)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
