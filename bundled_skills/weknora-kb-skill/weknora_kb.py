# -*- coding: utf-8 -*-
"""WeKnora 知识库查询 Skill

提供两个操作：
  list_knowledge_bases — 列出可用知识库
  search_knowledge     — 知识库检索，返回匹配文档片段
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─── WeKnora API 配置 ─────────────────────────────────────
BASE_URL = os.getenv("WEKNORA_BASE_URL", "http://127.0.0.1:8056/api/v1").rstrip("/")
# 不内置默认凭据：WeKnora 的 Key 与平台 API Key、LLM 网关 Key 互不通用，
# 部署时通过 WEKNORA_API_KEY 注入。
API_KEY = os.getenv("WEKNORA_API_KEY", "")

_http = requests.Session()
_http.headers.update({"Content-Type": "application/json"})
if API_KEY:
    _http.headers["X-API-Key"] = API_KEY
_adapter = HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504]))
_http.mount("http://", _adapter)
_http.mount("https://", _adapter)


def _api(method: str, path: str, **kwargs) -> dict:
    """请求 WeKnora API。"""
    resp = _http.request(method, f"{BASE_URL}{path}", timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def list_knowledge_bases() -> list[dict]:
    """列出所有知识库。"""
    result = _api("GET", "/knowledge-bases")
    kbs = result.get("data", [])
    if isinstance(kbs, dict):
        kbs = kbs.get("items", kbs.get("list", []))
    return [
        {"id": kb["id"], "name": kb["name"], "description": kb.get("description", "")}
        for kb in kbs
    ]


def search_knowledge(query: str, knowledge_base_ids: list[str],
                      knowledge_ids: list[str] | None = None) -> list[dict]:
    """搜索知识库文档片段（纯检索）。"""
    payload: dict = {"query": query, "knowledge_base_ids": knowledge_base_ids}
    if knowledge_ids:
        payload["knowledge_ids"] = knowledge_ids
    result = _api("POST", "/knowledge-search", json=payload)
    return [
        {
            "content": c.get("content", ""),
            "knowledge_title": c.get("knowledge_title", ""),
            "score": c.get("score", 0),
            "chunk_index": c.get("chunk_index", 0),
        }
        for c in result.get("data", [])
    ]


# ─── Action 路由 ───────────────────────────────────────────
ACTIONS = {
    "list_knowledge_bases": lambda args: list_knowledge_bases(),
    "search_knowledge": lambda args: search_knowledge(
        args.query,
        [x.strip() for x in args.knowledge_base_ids.split(",")],
        [x.strip() for x in args.knowledge_ids.split(",")] if args.knowledge_ids else None,
    ),
}


def main():
    parser = argparse.ArgumentParser(description="WeKnora 知识库查询工具")
    parser.add_argument("--action", required=True, choices=ACTIONS.keys(), help="要执行的操作")
    parser.add_argument("--query", help="搜索文本")
    parser.add_argument("--knowledge_base_ids", help="知识库ID列表(逗号分隔)")
    parser.add_argument("--knowledge_ids", help="文档ID列表(逗号分隔，可选)")

    args = parser.parse_args()

    if args.action == "search_knowledge" and not args.knowledge_base_ids:
        result = {"error": "search_knowledge 需要 --knowledge_base_ids，可先用 list_knowledge_bases 获取"}
    else:
        # 统一转成结构化错误：裸 traceback 对调用方（含 LLM）没有价值，
        # 还会把几十行堆栈灌进上下文。
        try:
            result = ACTIONS[args.action](args)
        except requests.exceptions.ConnectionError:
            result = {"error": f"无法连接 WeKnora 服务（{BASE_URL}）。"
                               f"请确认服务已启动，或用 WEKNORA_BASE_URL 指定正确地址。"}
        except requests.exceptions.Timeout:
            result = {"error": f"请求 WeKnora 服务超时（{BASE_URL}）"}
        except requests.exceptions.HTTPError as e:
            resp = e.response
            detail = ""
            if resp is not None:
                detail = (resp.text or "")[:200]
            status = resp.status_code if resp is not None else "?"
            result = {"error": f"WeKnora 返回 HTTP {status}: {detail}"}
        except requests.exceptions.RequestException as e:
            result = {"error": f"请求 WeKnora 失败: {e}"}
        except (KeyError, TypeError, ValueError) as e:
            result = {"error": f"解析 WeKnora 响应失败: {type(e).__name__}: {e}"}

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
