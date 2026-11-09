# -*- coding: utf-8 -*-
"""工单分析知识沉淀 → loca 知识库路径化写入。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_API_KEY = "loca_stude-default-mcp-key-2025"


def _base_url() -> str:
    return (os.environ.get("LOCA_STUDE_BACKEND_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("LOCA_STUDE_API_KEY") or _DEFAULT_API_KEY).strip()


def _headers() -> dict:
    return {
        "accept": "application/json, text/plain,*/*",
        "X-API-Key": _api_key(),
        "Content-Type": "application/json",
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

    if resp.status_code >= 400:
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)[:500]
        except Exception:
            detail = (resp.text or "")[:500]
        return {"error": f"HTTP {resp.status_code}: {detail}"}

    if resp.status_code == 204 or not (resp.text or "").strip():
        return {"ok": True}
    try:
        return _unwrap(resp.json())
    except Exception:
        return {"error": "响应不是合法 JSON"}


def _load_json_arg(direct: Optional[str], file_path: Optional[str]) -> Any:
    if file_path:
        raw = Path(file_path).read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            raise ValueError(f"无法解码 payload_file={file_path}")
        return json.loads(text)
    if not direct:
        return {}
    return json.loads(direct)


def _read_text_arg(direct: Optional[str], file_path: Optional[str]) -> str:
    if file_path:
        raw = Path(file_path).read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法解码 content_file={file_path}")
    return direct or ""


def _bullet_list(items) -> str:
    if not items:
        return "- （无）"
    if isinstance(items, str):
        return f"- {items}"
    lines = []
    for item in items:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _numbered_list(items) -> str:
    if not items:
        return "1. （无）"
    if isinstance(items, str):
        return f"1. {items}"
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))


def knowledge_doc_title(product_line: str, requirement_name: str, kind: str = "知识沉淀") -> str:
    pl = (product_line or "").strip()
    rn = (requirement_name or "").strip()
    kind = (kind or "知识沉淀").strip()
    if not pl or not rn:
        raise ValueError("product_line 与 requirement_name 必填")
    return f"{pl}/{rn}/{kind}"


def render_ticket_collect(product_line: str, requirement_name: str, payload: dict) -> str:
    """生成工单采集 Markdown；payload 可为部分字段，缺失处留空。"""
    p = payload or {}
    title = p.get("requirement_title") or requirement_name
    task_relations = p.get("task_relations_markdown") or (
        "| 类型 | ID | 标题 | 状态 | 执行者 | 迭代 | 链接 |\n"
        "|------|----|------|------|--------|------|------|\n"
        "| 原始任务 | | | | | | |\n"
        "| 父需求 | | | | | | |"
    )
    related = p.get("related_materials_markdown") or (
        "| 资料 | 地址 | 可读结果 | 摘要 |\n"
        "|------|------|----------|------|\n"
        "| 钉钉文档 | | | |"
    )
    comments = p.get("comments_markdown") or (
        "| SRC-ID | 时间 | 作者 | 类型 | 原文/摘要 | 来源 |\n"
        "|--------|------|------|------|-----------|------|"
    )
    conflicts = p.get("conflicts_markdown") or (
        "| 冲突项 | 来源 A | 来源 B | 处理 |\n"
        "|--------|--------|--------|------|"
    )
    evidence = p.get("evidence_markdown") or (
        "| SRC-ID | 来源类型 | 定位信息 | 作者/时间 | 可读结果 | 对应结论 |\n"
        "|--------|----------|----------|-----------|----------|----------|"
    )
    open_qs = _bullet_list(p.get("open_questions") or [])
    return f"""# {title} - 工单采集

## 采集信息

- 采集时间：{p.get('collected_at', '')}
- 用户提供的原始工单：{p.get('ticket_url', '')}
- 链接来源：{p.get('link_source', '用户输入')}
- 访问结果：{p.get('access_result', '')}
- 产品线：{product_line}
- 只读声明：本次仅执行读取、导航和展开，未修改任何外部内容

## 任务关系

{task_relations}

## 父需求当前备注

### 需求背景
{p.get('background', '')}

### 需求描述
{p.get('description', '')}

### 显式范围限制
{p.get('scope_limits', '')}

## 关联资料

{related}

## 父需求评论中的开发与测试信息

{comments}

## 影响范围

### 开发明确说明
{p.get('impact_dev', '')}

### 技术方案说明
{p.get('impact_tech', '')}

### 明确不影响
{p.get('impact_out', '')}

### 测试范围建议
{p.get('impact_test_suggestion', '')}

## 技术信息

- 开发分支：{p.get('dev_branch', '')}
- 技术工单：{p.get('tech_ticket', '')}
- 接口改动：{p.get('api_changes', '')}
- 表结构改动：{p.get('schema_changes', '')}
- 风险点：{p.get('risks', '')}
- 测试关注点：{p.get('test_focus', '')}

## 来源冲突

{conflicts}

## 缺失与待确认

{open_qs}

## 证据索引

{evidence}
"""


def render_knowledge_extract(product_line: str, requirement_name: str, payload: dict) -> str:
    p = payload or {}
    date = p.get("date") or ""
    fields = p.get("fields") or []
    field_rows = []
    for f in fields:
        if isinstance(f, dict):
            field_rows.append(
                f"| {f.get('name', '')} | {f.get('purpose', '')} | {f.get('rule', '')} | {f.get('relation', '')} |"
            )
        else:
            field_rows.append(f"| {f} | | | |")
    fields_table = (
        "| 字段或对象 | 用途 | 取值或回退规则 | 关联关系 |\n"
        "|------------|------|----------------|----------|\n"
        + ("\n".join(field_rows) if field_rows else "|  |  |  |  |")
    )
    return f"""# {product_line} / {requirement_name} - 知识沉淀

### {requirement_name}（{date}）

**来源**：{_bullet_list(p.get('sources') or [])}

**业务背景**：
{_bullet_list(p.get('business_background') or [])}

**涉及端、角色和系统**：
{_bullet_list(p.get('roles_systems') or [])}

**已确认核心规则**：
{_numbered_list(p.get('confirmed_rules') or [])}

**字段与数据模型**：
{fields_table}

**状态与流程**：
{_bullet_list(p.get('status_flow') or [])}

**配置、权限和范围**：
{_bullet_list(p.get('config_permission') or [])}

**测试必覆盖风险**：
{_bullet_list(p.get('test_risks') or [])}

**待确认项**：
{_bullet_list(p.get('open_questions') or [])}

**来源证据**：
{_bullet_list(p.get('evidence') or [])}
"""


def list_knowledge_bases(project_id: int):
    return _request(
        "GET",
        "knowledge/knowledge-bases/",
        params={"project": project_id, "is_active": "true"},
    )


def _find_document_by_title(knowledge_base_id: str, title: str) -> Optional[dict]:
    result = _request(
        "GET",
        "knowledge/documents/",
        params={
            "knowledge_base": knowledge_base_id,
            "search": title,
            "page_size": 50,
        },
    )
    if isinstance(result, dict) and result.get("error"):
        return None
    items = result
    if isinstance(result, dict):
        items = result.get("results") or result.get("documents") or result.get("data") or []
    if not isinstance(items, list):
        return None
    for doc in items:
        if isinstance(doc, dict) and (doc.get("title") or "") == title:
            return doc
    return None


def upsert_kb_document(knowledge_base_id: str, title: str, content: str):
    title = (title or "").strip()
    content = (content or "").strip()
    if not knowledge_base_id or not title or not content:
        return {"error": "knowledge_base_id、title、content 均必填"}

    existing = _find_document_by_title(knowledge_base_id, title)
    body = {
        "knowledge_base": knowledge_base_id,
        "title": title,
        "document_type": "md",
        "content": content,
    }
    if existing and existing.get("id"):
        doc_id = existing["id"]
        updated = _request(
            "PATCH",
            f"knowledge/documents/{doc_id}/",
            json_body={"title": title, "content": content},
        )
        if isinstance(updated, dict) and updated.get("error"):
            return updated
        # 更新正文后触发重新向量化，否则检索仍是旧 chunk
        reprocess = _request("POST", f"knowledge/documents/{doc_id}/reprocess/")
        return {
            "action": "updated",
            "document_id": doc_id,
            "title": title,
            "result": updated,
            "reprocess": reprocess,
        }

    created = _request("POST", "knowledge/documents/", json_body=body)
    if isinstance(created, dict) and created.get("error"):
        return created
    return {
        "action": "created",
        "document_id": (created or {}).get("id") if isinstance(created, dict) else None,
        "title": title,
        "result": created,
    }


def upsert_knowledge_extract(
    knowledge_base_id: str,
    product_line: str,
    requirement_name: str,
    payload: dict,
):
    content = render_knowledge_extract(product_line, requirement_name, payload)
    title = knowledge_doc_title(product_line, requirement_name, "知识沉淀")
    result = upsert_kb_document(knowledge_base_id, title, content)
    if isinstance(result, dict):
        result["content_preview"] = content[:400]
        result["kb_path"] = title
    return result


def main(argv: Optional[list] = None):
    parser = argparse.ArgumentParser(description="工单分析知识沉淀工具")
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "list_knowledge_bases",
            "render_ticket_collect",
            "render_knowledge_extract",
            "upsert_kb_document",
            "upsert_knowledge_extract",
        ],
    )
    parser.add_argument("--project_id", type=int)
    parser.add_argument("--knowledge_base_id")
    parser.add_argument("--product_line")
    parser.add_argument("--requirement_name")
    parser.add_argument("--title")
    parser.add_argument("--kind", default="知识沉淀")
    parser.add_argument("--payload")
    parser.add_argument("--payload_file")
    parser.add_argument("--content")
    parser.add_argument("--content_file")
    parser.add_argument("--output_file", help="渲染结果写入本地文件")

    args = parser.parse_args(argv)

    try:
        if args.action == "list_knowledge_bases":
            if not args.project_id:
                raise ValueError("--project_id 必填")
            result = list_knowledge_bases(args.project_id)
        elif args.action == "render_ticket_collect":
            payload = _load_json_arg(args.payload, args.payload_file)
            result_text = render_ticket_collect(
                args.product_line or "", args.requirement_name or "", payload
            )
            if args.output_file:
                Path(args.output_file).write_text(result_text, encoding="utf-8")
            result = {
                "title": knowledge_doc_title(
                    args.product_line or "未指定",
                    args.requirement_name or "未指定",
                    "工单采集",
                )
                if args.product_line and args.requirement_name
                else None,
                "markdown": result_text,
            }
        elif args.action == "render_knowledge_extract":
            payload = _load_json_arg(args.payload, args.payload_file)
            result_text = render_knowledge_extract(
                args.product_line or "", args.requirement_name or "", payload
            )
            if args.output_file:
                Path(args.output_file).write_text(result_text, encoding="utf-8")
            result = {
                "title": knowledge_doc_title(
                    args.product_line or "未指定",
                    args.requirement_name or "未指定",
                    "知识沉淀",
                )
                if args.product_line and args.requirement_name
                else None,
                "markdown": result_text,
            }
        elif args.action == "upsert_kb_document":
            content = _read_text_arg(args.content, args.content_file)
            result = upsert_kb_document(
                args.knowledge_base_id or "",
                args.title or "",
                content,
            )
        elif args.action == "upsert_knowledge_extract":
            payload = _load_json_arg(args.payload, args.payload_file)
            result = upsert_knowledge_extract(
                args.knowledge_base_id or "",
                args.product_line or "",
                args.requirement_name or "",
                payload,
            )
        else:
            result = {"error": f"未知 action: {args.action}"}
    except Exception as exc:
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
