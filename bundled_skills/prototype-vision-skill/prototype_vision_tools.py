# -*- coding: utf-8 -*-
"""原型识图需求整理工具。

强制流程：知识库检索通过 → 按模板组装 → 带 gate_token 才可写入需求文档正文。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import io
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    import requests
except ImportError:  # pragma: no cover - 运行环境需安装 requests
    requests = None  # type: ignore

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_API_KEY = "loca_stude-default-mcp-key-2025"

_REQUIRED_SECTIONS = (
    "背景与目标",
    "功能说明",
    "业务规则",
    "验收要点",
)


def _base_url() -> str:
    return (os.environ.get("LOCA_STUDE_BACKEND_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("LOCA_STUDE_API_KEY") or _DEFAULT_API_KEY).strip()


def _headers() -> dict:
    return {
        "accept": "application/json, text/plain,*/*",
        "Content-Type": "application/json",
        "X-API-Key": _api_key(),
    }


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and "status" in payload:
        return payload.get("data")
    return payload


def _request(method: str, path: str, *, params: dict = None, json_body: dict = None):
    if requests is None:
        return {"error": "未安装 requests，请先 pip install requests"}
    url = f"{_base_url()}/api/{path.lstrip('/')}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = requests.request(
            method,
            url,
            headers=_headers(),
            params=clean_params,
            json=json_body,
            timeout=60,
        )
    except Exception as exc:
        return {"error": f"请求失败: {exc}"}

    if resp.status_code == 403:
        return {
            "error": "无权访问（403）。请确认 project/knowledge_base 权限与 API Key。"
        }
    if resp.status_code == 404:
        return {"error": f"资源不存在（404）: {url}"}
    if resp.status_code >= 400:
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)[:400]
        except Exception:
            detail = (resp.text or "")[:400]
        return {"error": f"HTTP {resp.status_code}: {detail}"}

    if resp.status_code == 204 or not (resp.text or "").strip():
        return {"ok": True}

    try:
        return _unwrap(resp.json())
    except Exception:
        return {"error": "响应不是合法 JSON"}


def _read_text_arg(direct: Optional[str], file_path: Optional[str]) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return direct or ""


def _load_json_arg(direct: Optional[str], file_path: Optional[str]) -> Any:
    raw = _read_text_arg(direct, file_path).strip()
    if not raw:
        return None
    return json.loads(raw)


def _extract_hits(kb_payload: dict) -> list:
    if not isinstance(kb_payload, dict):
        return []
    sources = kb_payload.get("sources")
    if isinstance(sources, list) and sources:
        return sources
    for key in ("results", "chunks", "documents", "hits"):
        value = kb_payload.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def _gate_ok(kb_payload: dict) -> bool:
    hits = _extract_hits(kb_payload)
    answer = (kb_payload.get("answer") or "").strip()
    return bool(hits) or bool(answer)


def _make_gate_token(knowledge_base_id: str, query: str, kb_payload: dict) -> str:
    hit_titles = []
    for item in _extract_hits(kb_payload)[:8]:
        if isinstance(item, dict):
            hit_titles.append(
                str(
                    item.get("title")
                    or item.get("document_title")
                    or item.get("knowledge_title")
                    or item.get("source")
                    or ""
                )
            )
    material = "|".join(
        [
            str(knowledge_base_id),
            (query or "").strip(),
            (kb_payload.get("answer") or "")[:200],
            ",".join(hit_titles),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def list_knowledge_bases(project_id: int):
    data = _request(
        "GET",
        "knowledge/knowledge-bases/",
        params={"project": project_id, "page_size": 100},
    )
    if isinstance(data, dict) and data.get("error"):
        return data
    if isinstance(data, dict) and "results" in data:
        items = data.get("results") or []
    elif isinstance(data, list):
        items = data
    else:
        items = data
    slim = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        slim.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "project": item.get("project"),
                "is_active": item.get("is_active"),
                "document_count": item.get("document_count"),
            }
        )
    return {"project_id": project_id, "knowledge_bases": slim, "count": len(slim)}


def query_knowledge(
    knowledge_base_id: str,
    query: str,
    top_k: int = 5,
    similarity_threshold: float = 0.1,
):
    body = {
        "query": query,
        "knowledge_base_id": knowledge_base_id,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "include_metadata": True,
    }
    data = _request(
        "POST",
        f"knowledge/knowledge-bases/{knowledge_base_id}/query/",
        json_body=body,
    )
    if isinstance(data, dict) and data.get("error"):
        return data

    payload = data if isinstance(data, dict) else {"raw": data}
    ok = _gate_ok(payload)
    token = _make_gate_token(knowledge_base_id, query, payload) if ok else None
    return {
        **payload,
        "gate_ok": ok,
        "gate_token": token,
        "hit_count": len(_extract_hits(payload)),
        "message": (
            "知识库前置条件已满足，可继续 assemble_content / save_document_content"
            if ok
            else "知识库无命中，禁止保存；请更换 query 或补充知识库文档后再试"
        ),
    }


def _missing_required_sections(content: str) -> list[str]:
    missing = []
    for section in _REQUIRED_SECTIONS:
        # 允许 ## 2. 背景与目标 / ## 5. 功能说明（按页面/模块）
        pattern = rf"(?m)^##\s*(?:\d+(?:\.\d+)*\.?\s+)?{re.escape(section)}"
        if not re.search(pattern, content):
            missing.append(section)
    return missing


def assemble_content(
    vision_text: str,
    kb_payload: dict,
    title: str = "原型识图需求草稿",
):
    if not vision_text or not vision_text.strip():
        return {"error": "vision_text 为空，无法组装"}

    if not isinstance(kb_payload, dict):
        return {"error": "kb_payload 必须是 query_knowledge 返回的 JSON 对象"}

    if not kb_payload.get("gate_ok") and not _gate_ok(kb_payload):
        return {
            "error": "知识库前置条件未满足（gate_ok=false / 无命中），禁止组装保存稿",
            "format_ok": False,
            "gate_ok": False,
        }

    gate_token = kb_payload.get("gate_token") or _make_gate_token(
        str(kb_payload.get("knowledge_base_id") or ""),
        str(kb_payload.get("query") or ""),
        kb_payload,
    )

    hit_titles = []
    for item in _extract_hits(kb_payload):
        if isinstance(item, dict):
            name = (
                item.get("title")
                or item.get("document_title")
                or item.get("knowledge_title")
                or item.get("source")
            )
            if name:
                hit_titles.append(str(name))
    if not hit_titles and (kb_payload.get("answer") or "").strip():
        hit_titles.append("(知识库生成摘要)")

    kb_answer = (kb_payload.get("answer") or "").strip()
    content = f"""# {title}

## 1. 文档信息
- 来源：原型截图/识图
- 知识库依据：{', '.join(hit_titles) if hit_titles else '无'}
- gate_token：{gate_token}

## 2. 背景与目标
{kb_answer or '（请根据识图原文与知识库依据补全背景、目标）'}

## 3. 范围
### 3.1 In Scope
- （待补全）

### 3.2 Out of Scope
- （待补全）

## 4. 用户与场景
- （待补全）

## 5. 功能说明（按页面/模块）
### 5.1 识图要点汇总
（请将下方附录按页面拆成模块，补全入口/要素/交互/校验/权限）

## 6. 业务规则
- （待根据识图与知识库补全）

## 7. 数据与状态
- 字段：
- 状态流转：

## 8. 异常与边界
- （待补全）

## 9. 验收要点
- [ ] （待补全可验证条目）

## 10. 识图原文附录

{vision_text.strip()}
"""

    missing = _missing_required_sections(content)
    # 模板本身包含必填标题，但业务正文可能仍是占位；format_ok 以标题齐全为准，
    # 另给 placeholders 提示人工补全。
    placeholders = []
    for marker in ("待补全", "待根据"):
        if marker in content:
            placeholders.append(marker)

    return {
        "title": title,
        "content": content,
        "gate_token": gate_token,
        "gate_ok": True,
        "format_ok": len(missing) == 0,
        "missing_sections": missing,
        "has_placeholders": bool(placeholders),
        "message": (
            "已组装。请补全占位内容后再 save_document_content"
            if placeholders
            else "已组装且无占位提示，可带 gate_token 保存"
        ),
    }


def save_document_content(
    project_id: int,
    document_id: str,
    content: str,
    gate_token: str,
):
    if not gate_token or not str(gate_token).strip():
        return {
            "error": "缺少 gate_token。必须先 query_knowledge 且 gate_ok=true 后再保存。"
        }
    if not content or not content.strip():
        return {"error": "content 为空，拒绝保存"}

    missing = _missing_required_sections(content)
    if missing:
        return {
            "error": f"正文缺少必填章节，拒绝保存: {', '.join(missing)}",
            "missing_sections": missing,
        }

    if "gate_token" in content and gate_token not in content:
        return {
            "error": "正文中的 gate_token 与参数不一致，拒绝保存（请使用 assemble_content 产出的正文）"
        }

    # 二次确认文档属于项目
    detail = _request(
        "GET",
        f"requirements/documents/{document_id}/",
        params={"project": project_id},
    )
    if isinstance(detail, dict) and detail.get("error"):
        return detail
    if isinstance(detail, dict):
        doc_project = detail.get("project")
        if doc_project is not None and int(doc_project) != int(project_id):
            return {"error": f"文档不属于 project_id={project_id}"}

    result = _request(
        "PATCH",
        f"requirements/documents/{document_id}/",
        params={"project": project_id},
        json_body={"content": content},
    )
    if isinstance(result, dict) and result.get("error"):
        return result

    return {
        "ok": True,
        "document_id": document_id,
        "project_id": project_id,
        "content_length": len(content),
        "gate_token": gate_token,
        "message": "需求正文已保存（已校验知识库 gate 与必填章节）",
    }


def main(argv: Optional[list] = None):
    parser = argparse.ArgumentParser(description="原型识图需求整理（知识库前置保存）")
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "list_knowledge_bases",
            "query_knowledge",
            "assemble_content",
            "save_document_content",
        ],
    )
    parser.add_argument("--project_id", type=int)
    parser.add_argument("--knowledge_base_id")
    parser.add_argument("--document_id")
    parser.add_argument("--query")
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--similarity_threshold", type=float, default=0.1)
    parser.add_argument("--title")
    parser.add_argument("--vision_text")
    parser.add_argument("--vision_file")
    parser.add_argument("--kb_payload")
    parser.add_argument("--kb_payload_file")
    parser.add_argument("--content")
    parser.add_argument("--content_file")
    parser.add_argument("--gate_token")

    args = parser.parse_args(argv)

    try:
        if args.action == "list_knowledge_bases":
            if args.project_id is None:
                result = {"error": "list_knowledge_bases 需要 --project_id"}
            else:
                result = list_knowledge_bases(args.project_id)
        elif args.action == "query_knowledge":
            if not args.knowledge_base_id or not args.query:
                result = {
                    "error": "query_knowledge 需要 --knowledge_base_id 与 --query"
                }
            else:
                result = query_knowledge(
                    args.knowledge_base_id,
                    args.query,
                    args.top_k,
                    args.similarity_threshold,
                )
        elif args.action == "assemble_content":
            vision = _read_text_arg(args.vision_text, args.vision_file)
            kb_payload = _load_json_arg(args.kb_payload, args.kb_payload_file)
            if kb_payload is None:
                result = {
                    "error": "assemble_content 需要 --kb_payload 或 --kb_payload_file"
                }
            else:
                result = assemble_content(
                    vision, kb_payload, title=args.title or "原型识图需求草稿"
                )
        else:
            if args.project_id is None or not args.document_id or not args.gate_token:
                result = {
                    "error": "save_document_content 需要 --project_id --document_id --gate_token"
                }
            else:
                content = _read_text_arg(args.content, args.content_file)
                result = save_document_content(
                    args.project_id, args.document_id, content, args.gate_token
                )
    except Exception as exc:
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
