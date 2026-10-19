"""钉钉开放平台客户端：token、知识库节点、文档正文。"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urlparse

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

API_BASE = "https://api.dingtalk.com"
OAPI_BASE = "https://oapi.dingtalk.com"
_TEMPORARY_ERROR_HINTS = (
    "temporary failure",
    "temporarily unavailable",
    "service unavailable",
    "try again",
    "too many requests",
    "throttl",
    "rate limit",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
)

# /i/nodes/{dentryUuid}；分享短链 /i/p/ 不能直接当 docKey。
_NODES_UUID_RE = re.compile(r"/i/nodes/([^/?#]+)", re.IGNORECASE)
_SHARE_SHORT_RE = re.compile(r"/i/p/", re.IGNORECASE)
# blocks 接口只覆盖钉钉富文本文档；表格/脑图等要用别的能力。
_UNSUPPORTED_CONTENT_EXTENSIONS = {
    "axsheet",
    "xlsx",
    "xls",
    "csv",
    "mindmap",
    "whiteboard",
    "able",
    "flippage",
    "ppt",
    "pptx",
    "pdf",
    "doc",
    "docx",
}


class DingTalkAPIError(Exception):
    """钉钉 API 调用失败。"""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    @property
    def is_temporary(self) -> bool:
        if self.status_code in (408, 429, 500, 502, 503, 504):
            return True
        text = str(self).lower()
        return any(hint in text for hint in _TEMPORARY_ERROR_HINTS)


class DingTalkClient:
    """基于 DingTalkConfig 的只读客户端。"""

    def __init__(self, config=None):
        from .models import DingTalkConfig

        self.config = config or DingTalkConfig.get_config()

    def ensure_enabled(self):
        if not self.config.enabled:
            raise DingTalkAPIError("钉钉同步未启用，请先在「钉钉配置」中启用并填写凭证")
        if not self.config.app_key or not self.config.app_secret:
            raise DingTalkAPIError("缺少 AppKey / AppSecret")
        if not self.config.operator_user_id and not self.config.operator_union_id:
            raise DingTalkAPIError("缺少操作人 User ID")

    def get_access_token(self, force_refresh: bool = False) -> str:
        self.ensure_enabled()
        now = timezone.now()
        if (
            not force_refresh
            and self.config.access_token
            and self.config.access_token_expires_at
            and self.config.access_token_expires_at > now + timedelta(minutes=2)
        ):
            return self.config.access_token

        resp = requests.post(
            f"{API_BASE}/v1.0/oauth2/accessToken",
            json={"appKey": self.config.app_key, "appSecret": self.config.app_secret},
            timeout=30,
        )
        data = self._parse_json(resp)
        token = data.get("accessToken") or data.get("access_token")
        expire_in = int(data.get("expireIn") or data.get("expires_in") or 7200)
        if not token:
            raise DingTalkAPIError("获取 accessToken 失败", resp.status_code, data)

        self.config.access_token = token
        self.config.access_token_expires_at = now + timedelta(seconds=max(expire_in - 60, 60))
        self.config.save(
            update_fields=["access_token", "access_token_expires_at", "updated_at"]
        )
        return token

    def get_operator_union_id(self, force_refresh: bool = False) -> str:
        self.ensure_enabled()
        if self.config.operator_union_id and not force_refresh:
            return self.config.operator_union_id
        if not self.config.operator_user_id:
            raise DingTalkAPIError("缺少操作人 User ID，无法解析 unionId")

        token = self.get_access_token()
        # 新版通讯录
        resp = requests.get(
            f"{API_BASE}/v1.0/contact/users/{self.config.operator_user_id}",
            headers=self._headers(token),
            timeout=30,
        )
        if resp.status_code == 200:
            data = self._parse_json(resp)
            union_id = (
                data.get("unionId")
                or data.get("unionid")
                or (data.get("result") or {}).get("unionId")
                or (data.get("result") or {}).get("unionid")
            )
            if union_id:
                self.config.operator_union_id = union_id
                self.config.save(update_fields=["operator_union_id", "updated_at"])
                return union_id

        # 旧版 topapi 兜底
        resp = requests.post(
            f"{OAPI_BASE}/topapi/v2/user/get",
            params={"access_token": token},
            json={"userid": self.config.operator_user_id},
            timeout=30,
        )
        data = self._parse_json(resp)
        result = data.get("result") or {}
        union_id = result.get("unionid") or result.get("unionId")
        if not union_id:
            raise DingTalkAPIError(
                f"无法通过 User ID 解析 unionId: {data.get('errmsg') or data}",
                resp.status_code,
                data,
            )
        self.config.operator_union_id = union_id
        self.config.save(update_fields=["operator_union_id", "updated_at"])
        return union_id

    def test_connection(self) -> Dict[str, Any]:
        """换 token + 解析 unionId + 拉知识库列表，用于配置页「测试连接」。"""
        token = self.get_access_token(force_refresh=True)
        union_id = self.get_operator_union_id(force_refresh=True)
        workspaces = self.list_workspaces()
        return {
            "ok": True,
            "operator_union_id": union_id,
            "workspace_count": len(workspaces),
            "workspaces": [
                {
                    "workspace_id": w.get("workspaceId") or w.get("workspace_id"),
                    "name": w.get("name") or w.get("workspaceName") or "",
                    "root_node_id": w.get("rootNodeId") or w.get("root_node_id") or "",
                }
                for w in workspaces[:20]
            ],
            "token_prefix": (token[:8] + "...") if token else "",
        }

    def list_workspaces(self) -> List[Dict[str, Any]]:
        token = self.get_access_token()
        operator_id = self.get_operator_union_id()
        items: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"operatorId": operator_id, "maxResults": 50}
            if next_token:
                params["nextToken"] = next_token
            resp = requests.get(
                f"{API_BASE}/v2.0/wiki/workspaces",
                headers=self._headers(token),
                params=params,
                timeout=30,
            )
            data = self._parse_json(resp, expect_ok=True)
            batch = data.get("workspaces") or data.get("items") or []
            items.extend(batch)
            next_token = data.get("nextToken")
            if not next_token:
                break
        return items

    def get_workspace(self, workspace_id: str) -> Dict[str, Any]:
        for ws in self.list_workspaces():
            wid = ws.get("workspaceId") or ws.get("workspace_id")
            if wid == workspace_id:
                return ws
        raise DingTalkAPIError(f"未找到钉钉知识库: {workspace_id}")

    @staticmethod
    def extract_dentry_uuid_from_url(url: str) -> Optional[str]:
        """从 /i/nodes/{uuid} 链接提取 dentryUuid；分享短链 /i/p/ 返回 None。"""
        if not url:
            return None
        text = unquote(url.strip())
        if _SHARE_SHORT_RE.search(text):
            return None
        match = _NODES_UUID_RE.search(text)
        if match:
            return match.group(1).strip() or None
        # document/edit?dentryKey=... 不是 blocks 可用的 docKey，交给 queryByUrl 解析
        return None

    @staticmethod
    def is_share_short_url(url: str) -> bool:
        return bool(url and _SHARE_SHORT_RE.search(unquote(url)))

    @classmethod
    def resolve_document_key(cls, node: Dict[str, Any], fallback_url: str = "") -> str:
        """正文接口优先用 URL 里的 dentryUuid，其次用 wiki 的 nodeId。"""
        for candidate in (
            cls.extract_dentry_uuid_from_url(node.get("url") or ""),
            cls.extract_dentry_uuid_from_url(fallback_url),
            (node.get("nodeId") or "").strip(),
        ):
            if candidate:
                return candidate
        raise DingTalkAPIError("节点缺少可用的文档 ID（dentryUuid / nodeId）")

    @classmethod
    def describe_unsupported_content(cls, node: Dict[str, Any], url: str = "") -> Optional[str]:
        """若节点不是可拉取正文的钉钉富文本，返回原因；否则返回 None。"""
        category = (node.get("category") or "").upper()
        node_type = (node.get("type") or "").upper()
        extension = (node.get("extension") or "").lower().lstrip(".")
        name = node.get("name") or node.get("nodeId") or "该节点"

        if cls.is_share_short_url(url) and not cls.extract_dentry_uuid_from_url(
            node.get("url") or ""
        ):
            return (
                f"「{name}」链接是钉钉分享短链（/i/p/）。请改用知识库里的节点链接"
                "（形如 https://alidocs.dingtalk.com/i/nodes/xxxxx）后再导入。"
            )

        if node_type == "FOLDER":
            return (
                f"「{name}」是文件夹，不能按文档导入正文。"
                "请绑定目录同步，或贴单个文档链接。"
            )

        if extension in _UNSUPPORTED_CONTENT_EXTENSIONS:
            return (
                f"「{name}」类型为 .{extension}，当前只支持钉钉富文本文档（.adoc）。"
                "表格请导出为 xlsx 后上传；脑图/白板请导出为文本或截图说明后再导入。"
            )

        if category and category not in ("ALIDOC",):
            return (
                f"「{name}」类别为 {category}，不是钉钉在线文档（ALIDOC）。"
                "本地附件/图片等请下载后上传到知识库。"
            )
        return None

    def get_node_by_url(self, url: str) -> Dict[str, Any]:
        if self.is_share_short_url(url):
            # 短链有时仍能解析，但失败时错误会更明确；先尝试 API。
            logger.info("钉钉链接为分享短链，尝试 queryByUrl: %s", url[:120])
        token = self.get_access_token()
        operator_id = self.get_operator_union_id()
        resp = requests.post(
            f"{API_BASE}/v2.0/wiki/nodes/queryByUrl",
            headers=self._headers(token),
            params={"operatorId": operator_id},
            json={"url": url},
            timeout=30,
        )
        data = self._parse_json(resp, expect_ok=True)
        node = data.get("node") or data
        if not node.get("nodeId"):
            raise DingTalkAPIError("通过链接未解析到节点", resp.status_code, data)
        if not node.get("url"):
            node = dict(node)
            node["url"] = url
        return node

    def get_node(self, node_id: str) -> Dict[str, Any]:
        token = self.get_access_token()
        operator_id = self.get_operator_union_id()
        resp = requests.get(
            f"{API_BASE}/v2.0/wiki/nodes/{node_id}",
            headers=self._headers(token),
            params={"operatorId": operator_id},
            timeout=30,
        )
        data = self._parse_json(resp, expect_ok=True)
        return data.get("node") or data

    def list_child_nodes(self, parent_node_id: str) -> List[Dict[str, Any]]:
        token = self.get_access_token()
        operator_id = self.get_operator_union_id()
        items: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {
                "parentNodeId": parent_node_id,
                "operatorId": operator_id,
                "maxResults": 50,
            }
            if next_token:
                params["nextToken"] = next_token
            resp = requests.get(
                f"{API_BASE}/v2.0/wiki/nodes",
                headers=self._headers(token),
                params=params,
                timeout=30,
            )
            data = self._parse_json(resp, expect_ok=True)
            items.extend(data.get("nodes") or [])
            next_token = data.get("nextToken")
            if not next_token:
                break
        return items

    def walk_nodes(
        self,
        parent_node_id: str,
        path_prefix: str = "",
    ) -> Iterable[Dict[str, Any]]:
        """递归遍历目录树，产出带 path 的节点字典。"""
        for node in self.list_child_nodes(parent_node_id):
            name = node.get("name") or ""
            path = f"{path_prefix}/{name}".strip("/") if path_prefix else name
            node = dict(node)
            node["_path"] = path
            yield node
            if (node.get("type") or "").upper() == "FOLDER" or node.get("hasChildren"):
                yield from self.walk_nodes(node["nodeId"], path)

    def get_document_markdown(self, doc_key: str, hint_url: str = "") -> str:
        """拉取文档正文并转为 Markdown。

        钉钉开放平台没有「GET /content 读 Markdown」接口；
        /content 是写入（POST）。正文只能走 blocks。
        """
        keys: List[str] = []
        for candidate in (
            self.extract_dentry_uuid_from_url(hint_url),
            (doc_key or "").strip(),
        ):
            if candidate and candidate not in keys:
                keys.append(candidate)
        if not keys:
            raise DingTalkAPIError("缺少文档 ID，无法拉取正文")

        errors: List[str] = []
        saw_temporary = False
        for key in keys:
            try:
                blocks = self.get_document_blocks(key)
            except DingTalkAPIError as e:
                errors.append(f"{key}: {e}")
                if e.is_temporary:
                    saw_temporary = True
                logger.warning("blocks 拉取失败 docKey=%s: %s", key, e)
                continue
            md = blocks_to_markdown(blocks)
            if md.strip():
                return md
            if blocks:
                logger.warning(
                    "blocks 非空但转 Markdown 为空 docKey=%s count=%s",
                    key,
                    len(blocks),
                )
                errors.append(f"{key}: 有块但无法提取文本（可能是表格/脑图等非正文块）")
            else:
                errors.append(f"{key}: 文档块为空")

        if saw_temporary:
            hint = "钉钉接口暂时失败，请稍后对该文档点「重新处理」；批量导入时也可稍后再试。"
        else:
            hint = (
                "请确认：1) 链接是 /i/nodes/ 节点地址而非 /i/p/ 分享短链；"
                "2) 目标是钉钉富文本文档（.adoc）而非表格/脑图/附件；"
                "3) 应用已开通「企业存储文件读权限 Storage.File.Read」，"
                "且操作人能打开该文档。"
            )
        raise DingTalkAPIError(
            f"无法获取文档正文；{'；'.join(errors)}。{hint}"
        )

    def get_document_blocks(self, doc_key: str, *, retries: int = 3) -> List[Dict[str, Any]]:
        token = self.get_access_token()
        operator_id = self.get_operator_union_id()
        blocks: List[Dict[str, Any]] = []
        start_index = 0
        page_size = 50
        while True:
            end_index = start_index + page_size
            data = self._request_blocks_page(
                doc_key,
                token=token,
                operator_id=operator_id,
                start_index=start_index,
                end_index=end_index,
                retries=retries,
            )
            batch = self._extract_blocks_list(data)
            if not batch:
                break
            blocks.extend(batch)
            if len(batch) < page_size:
                break
            start_index = end_index
        return blocks

    def _request_blocks_page(
        self,
        doc_key: str,
        *,
        token: str,
        operator_id: str,
        start_index: int,
        end_index: int,
        retries: int = 3,
    ) -> Dict[str, Any]:
        last_error: Exception | None = None
        attempts = max(1, retries)
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.get(
                    f"{API_BASE}/v1.0/doc/suites/documents/{doc_key}/blocks",
                    headers=self._headers(token),
                    params={
                        "operatorId": operator_id,
                        "startIndex": start_index,
                        "endIndex": end_index,
                    },
                    timeout=60,
                )
                return self._parse_json(resp, expect_ok=True)
            except (DingTalkAPIError, requests.RequestException) as e:
                last_error = e
                temporary = (
                    e.is_temporary
                    if isinstance(e, DingTalkAPIError)
                    else True
                )
                if not temporary or attempt >= attempts:
                    if isinstance(e, DingTalkAPIError):
                        raise
                    raise DingTalkAPIError(str(e)) from e
                sleep_s = min(8.0, 1.5 * attempt)
                logger.warning(
                    "blocks 临时失败，%ss 后重试 %s/%s docKey=%s: %s",
                    sleep_s,
                    attempt,
                    attempts,
                    doc_key,
                    e,
                )
                time.sleep(sleep_s)
                # 临时失败时刷新 token，避免过期令牌连错
                token = self.get_access_token(force_refresh=(attempt >= 2))
        raise DingTalkAPIError(str(last_error) if last_error else "blocks 请求失败")

    @staticmethod
    def _extract_blocks_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """兼容钉钉多种返回结构：result.data / data / blocks。"""
        if not isinstance(data, dict):
            return []
        candidates = [
            data.get("data"),
            data.get("blocks"),
        ]
        result = data.get("result")
        if isinstance(result, list):
            candidates.append(result)
        elif isinstance(result, dict):
            candidates.extend(
                [
                    result.get("data"),
                    result.get("blocks"),
                    result.get("elements"),
                ]
            )
        for item in candidates:
            if isinstance(item, list):
                return item
            if isinstance(item, dict):
                nested = (
                    item.get("data")
                    or item.get("blocks")
                    or item.get("elements")
                    or item.get("list")
                )
                if isinstance(nested, list):
                    return nested
        return []

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

    def _parse_json(self, resp: requests.Response, expect_ok: bool = False) -> Dict[str, Any]:
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            msg = (
                data.get("message")
                or data.get("errmsg")
                or data.get("errorMsg")
                or data.get("msg")
                or resp.text
                or f"HTTP {resp.status_code}"
            )
            raise DingTalkAPIError(str(msg), resp.status_code, data)
        if expect_ok and isinstance(data, dict):
            # 部分旧接口用 errcode
            errcode = data.get("errcode")
            if errcode not in (None, 0, "0"):
                raise DingTalkAPIError(
                    data.get("errmsg") or f"errcode={errcode}",
                    resp.status_code,
                    data,
                )
        return data if isinstance(data, dict) else {"data": data}


def parse_dingtalk_modified_time(node: Dict[str, Any]):
    """解析节点修改时间，返回 aware datetime 或 None。"""
    ts = node.get("modifiedTimestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(int(ts) / 1000.0, tz=dt_timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    raw = node.get("modifiedTime") or node.get("modified_at")
    if not raw:
        return None
    dt = parse_datetime(str(raw).replace("Z", "+00:00"))
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def is_dingtalk_doc_url(url: str) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return "dingtalk.com" in host or "alidocs" in host


def blocks_to_markdown(blocks: List[Dict[str, Any]]) -> str:
    """将钉钉一级块粗转为 Markdown（检索优先，不追求 100% 保真）。"""
    lines: List[str] = []
    for block in blocks or []:
        try:
            text = _block_to_markdown(block)
        except Exception as e:
            logger.warning("块转 Markdown 失败，降级为纯文本: %s", e)
            text = _inline_text(block)
        if text is not None and str(text).strip():
            lines.append(str(text).rstrip())
    return "\n\n".join(lines).strip() + ("\n" if lines else "")


def _parse_heading_level(raw: Any, default: int = 1) -> int:
    """兼容 level=5 / 'heading-5' / 'h5' 等钉钉标题层级写法。"""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return max(1, min(raw, 6))
    text = str(raw).strip().lower()
    if not text:
        return default
    if text.isdigit():
        return max(1, min(int(text), 6))
    import re

    match = re.search(r"(\d+)", text)
    if match:
        return max(1, min(int(match.group(1)), 6))
    return default


def _block_to_markdown(block: Dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return str(block)

    block_type = (
        block.get("blockType")
        or block.get("type")
        or block.get("elementType")
        or ""
    ).lower()

    # 嵌套：element / paragraph / heading 等
    if "paragraph" in block or block_type == "paragraph":
        para = block.get("paragraph") or block
        return _inline_text(para)

    if "heading" in block or block_type.startswith("heading") or block_type == "header":
        heading = block.get("heading") or block.get("header") or block
        level = _parse_heading_level(
            heading.get("level") or heading.get("headingLevel") or block_type,
            default=1,
        )
        text = _inline_text(heading)
        return f"{'#' * level} {text}".strip()

    if block_type in ("bullet", "bulletedlist", "unorderedlist", "list") or "bullet" in block:
        items = block.get("bullet") or block.get("items") or block
        return _list_markdown(items, ordered=False)

    if block_type in ("ordered", "orderedlist", "numberedlist") or "ordered" in block:
        items = block.get("ordered") or block.get("items") or block
        return _list_markdown(items, ordered=True)

    if block_type == "code" or "code" in block:
        code = block.get("code") or block
        lang = code.get("language") or ""
        body = code.get("text") or code.get("content") or _inline_text(code)
        return f"```{lang}\n{body}\n```"

    if block_type == "quote" or "quote" in block:
        quote = block.get("quote") or block
        body = _inline_text(quote)
        return "\n".join(f"> {line}" for line in body.splitlines() or [body])

    if block_type == "table" or "table" in block:
        return _table_markdown(block.get("table") or block)

    if block_type in ("divider", "hr"):
        return "---"

    # 通用：抽 children / text
    text = _inline_text(block)
    return text


def _list_markdown(items: Any, ordered: bool) -> str:
    if isinstance(items, dict):
        children = items.get("children") or items.get("items") or [items]
    elif isinstance(items, list):
        children = items
    else:
        return f"{'1.' if ordered else '-'} {_inline_text(items)}"

    lines = []
    for i, item in enumerate(children, start=1):
        prefix = f"{i}." if ordered else "-"
        lines.append(f"{prefix} {_inline_text(item)}")
    return "\n".join(lines)


def _table_markdown(table: Dict[str, Any]) -> str:
    rows = table.get("rows") or table.get("cells") or []
    if not rows:
        return _inline_text(table)
    md_rows: List[str] = []
    for idx, row in enumerate(rows):
        cells = row if isinstance(row, list) else (row.get("cells") or row.get("values") or [])
        texts = [_inline_text(c).replace("|", "\\|") for c in cells]
        md_rows.append("| " + " | ".join(texts) + " |")
        if idx == 0:
            md_rows.append("| " + " | ".join("---" for _ in texts) + " |")
    return "\n".join(md_rows)


def _inline_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, list):
        return "".join(_inline_text(x) for x in node)
    if not isinstance(node, dict):
        return str(node)

    for key in ("text", "content", "plainText", "title", "value"):
        if key in node and isinstance(node[key], str):
            return node[key]

    parts: List[str] = []
    for key in ("children", "elements", "runs", "spans", "textRuns"):
        if key in node and isinstance(node[key], list):
            parts.append(_inline_text(node[key]))
    if parts:
        return "".join(parts)

    # 浅层拼接字符串叶子
    for v in node.values():
        if isinstance(v, str) and v.strip():
            parts.append(v)
        elif isinstance(v, list):
            parts.append(_inline_text(v))
        elif isinstance(v, dict) and any(
            k in v for k in ("text", "children", "elements")
        ):
            parts.append(_inline_text(v))
    return "".join(parts)
