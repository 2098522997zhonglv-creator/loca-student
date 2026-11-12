"""统一 ToolResult 协议（FR01）。

Skill / MCP / 检索出口逐步适配；模型侧收到 JSON 文本，完整日志可另存 output_ref。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_EXIT_CODE_RE = re.compile(r"退出码[：:\s]*(-?\d+)|exit(?:\s*code)?[：:=\s]*(-?\d+)", re.I)
_TIMEOUT_RE = re.compile(r"timeout|timed out|超时", re.I)
_FILECHOOSER_RE = re.compile(r"filechooser|waiting for event\s*[\"']filechooser", re.I)
_SET_INPUT_RE = re.compile(r"setInputFiles|input\[type\s*=\s*[\"']file[\"']\]", re.I)


@dataclass
class ToolResult:
    status: str  # succeeded | failed | unknown | needs_confirmation | skipped | cancelled
    operation_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    retryable: bool = False
    evidence: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    exit_code: Optional[int] = None
    output_ref: str = ""
    execution_method: str = ""  # native_dialog | filechooser | set_input | api_upload | ""
    resource_type: str = ""  # cart_id | cart_item_id | checkout_id | order_id | order_sn | ""
    resource_id: str = ""
    source_url: str = ""
    observed_at: str = ""
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_tool_text(self) -> str:
        """给模型看的结构化文本：状态可计算，摘要可读。"""
        payload = self.to_dict()
        # 避免超大 stdout 塞进 data
        raw = payload.get("data", {}).get("raw_text")
        if isinstance(raw, str) and len(raw) > 4000:
            payload["data"] = dict(payload["data"])
            payload["data"]["raw_text"] = (
                raw[:2500] + f"\n…[截断 {len(raw) - 2700} 字符]…\n" + raw[-200:]
            )
            if not payload.get("output_ref"):
                payload["output_ref"] = f"inline:{self.operation_id}"
        return json.dumps(payload, ensure_ascii=False, indent=2)


def new_operation_id() -> str:
    return uuid.uuid4().hex[:16]


def infer_execution_method(text: str) -> str:
    blob = text or ""
    if _FILECHOOSER_RE.search(blob) and "timeout" in blob.lower():
        return "filechooser_failed"
    if _FILECHOOSER_RE.search(blob):
        return "filechooser"
    if _SET_INPUT_RE.search(blob):
        return "set_input"
    if re.search(r"原生|native.*(file|dialog)|真实文件选择", blob, re.I):
        return "native_dialog"
    return ""


_ID_FIELD_RE = re.compile(
    r"(cart_id|cart_item_id|checkout_id|order_id|order_sn)\s*[:=]\s*"
    r"(?!unknown\b|null\b|none\b|n/?a\b|undefined\b)([A-Za-z0-9][A-Za-z0-9_-]{2,})",
    re.I,
)
_ID_KEYS = (
    "cart_id",
    "cart_item_id",
    "checkout_id",
    "order_id",
    "order_sn",
    "case_id",
    "testcase_id",
    "execution_id",
)
_BUSINESS_REJECT_RE = re.compile(
    r"余额不足|库存不足|权限不足|无权限|不存在|状态不允许|业务校验|"
    r"business[_ ]?error|rejected by policy|permission denied|"
    r"validation failed|invalid payload",
    re.I,
)
# 「已存在」仅在缺少明确成功字段时作为拒绝兜底（R02）
_EXISTS_REJECT_RE = re.compile(r"已存在|already exists", re.I)
_SOURCE_URL_RE = re.compile(
    r"(?:source_url|product_url|url)\s*[:=]\s*['\"]?(https?://\S+|/[^\s'\"]+)",
    re.I,
)


def _try_parse_json_blob(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _walk_collect_ids(obj: Any, found: Dict[str, str], depth: int = 0) -> None:
    if depth > 6 or obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if key in _ID_KEYS and v not in (None, "", "unknown", "null", "none"):
                found[key] = str(v)
            elif key == "data" and isinstance(v, dict):
                _walk_collect_ids(v, found, depth + 1)
            else:
                _walk_collect_ids(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:50]:
            _walk_collect_ids(item, found, depth + 1)


def _structured_business_status(payload: Dict[str, Any]) -> Optional[str]:
    """尊重明确业务字段；返回 failed/succeeded/cancelled/skipped/unknown 或 None。"""
    if not isinstance(payload, dict):
        return None
    # ToolResult 嵌套
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else None
    for node in (payload, nested):
        if not isinstance(node, dict):
            continue
        if "success" in node:
            return "succeeded" if bool(node.get("success")) else "failed"
        if "ok" in node and isinstance(node.get("ok"), bool):
            return "succeeded" if node.get("ok") else "failed"
        status = str(node.get("status") or "").strip().lower()
        if status in {
            "failed",
            "error",
            "rejected",
            "business_rejected",
        }:
            return "failed"
        if status in {"succeeded", "success", "ok", "completed"}:
            return "succeeded"
        if status in {"cancelled", "canceled"}:
            return "cancelled"
        if status in {"skipped"}:
            return "skipped"
        if status in {"unknown"}:
            return "unknown"
        if status == "needs_confirmation":
            return "needs_confirmation"
        code = node.get("code")
        if code is not None and str(code).isdigit() and int(code) >= 400:
            return "failed"
        err = node.get("error")
        if err not in (None, "", False, {}):
            # 有明确 error 且无 success:true → 失败
            if node.get("success") is not True and node.get("ok") is not True:
                return "failed"
    return None


def extract_typed_identifiers(text: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """从工具输出提取类型化业务标识（忽略 unknown/null）；优先 JSON。"""
    found: Dict[str, str] = {}
    if payload is not None:
        _walk_collect_ids(payload, found)
    for m in _ID_FIELD_RE.finditer(text or ""):
        found.setdefault(m.group(1).lower(), m.group(2))
    return found


def extract_source_url(text: str, payload: Optional[Dict[str, Any]] = None) -> str:
    if isinstance(payload, dict):
        for key in ("source_url", "product_url", "url"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            data = payload.get("data")
            if isinstance(data, dict):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    m = _SOURCE_URL_RE.search(text or "")
    return m.group(1).rstrip(".,)") if m else ""


def classify_skill_output(
    *,
    text: str,
    exit_code: Optional[int] = None,
    duration_ms: float = 0.0,
    skill_name: str = "",
    command: str = "",
) -> ToolResult:
    """从 skill 文本出口推断 ToolResult（适配器）。"""
    raw = (text or "").strip()
    op_id = new_operation_id()
    observed = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    parsed = _try_parse_json_blob(raw)

    # needs_confirmation JSON
    if parsed and (
        parsed.get("status") == "needs_confirmation"
        or "needs_confirmation" in raw
    ):
        if parsed.get("status") == "needs_confirmation":
            return ToolResult(
                status="needs_confirmation",
                operation_id=op_id,
                data=parsed,
                error_code="needs_confirmation",
                retryable=False,
                evidence={"pending": parsed.get("pending")},
                duration_ms=duration_ms,
                summary=str(parsed.get("message") or "需要写入确认")[:500],
                observed_at=observed,
            )

    code = exit_code
    if code is None:
        m = _EXIT_CODE_RE.search(raw)
        if m:
            try:
                code = int(m.group(1) or m.group(2))
            except (TypeError, ValueError):
                code = None

    method = infer_execution_method(raw)
    ids = extract_typed_identifiers(raw, parsed)
    source_url = extract_source_url(raw, parsed)
    primary_type = next((k for k in _ID_KEYS if k in ids), next(iter(ids), ""))
    primary_id = ids.get(primary_type, "") if primary_type else ""
    evidence: Dict[str, Any] = {
        "skill_name": skill_name or "",
        "command_preview": (command or "")[:240],
    }
    if method:
        evidence["execution_method"] = method
    if ids:
        evidence["identifiers"] = ids
    if source_url:
        evidence["source_url"] = source_url

    structured_data: Dict[str, Any] = {"raw_text": raw}
    if parsed:
        structured_data.update(parsed)
        # 展平 data.* 便于 ${created.case_id} 引用（R24）
        nested = parsed.get("data")
        if isinstance(nested, dict):
            for k, v in nested.items():
                structured_data.setdefault(k, v)
    if ids:
        structured_data["identifiers"] = ids

    def _with_meta(tr: ToolResult) -> ToolResult:
        if primary_type:
            tr.resource_type = primary_type
            tr.resource_id = primary_id
        if source_url:
            tr.source_url = source_url
        if method:
            tr.execution_method = method
        return tr

    if code is not None and code != 0:
        return _with_meta(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data=structured_data,
                error_code="nonzero_exit",
                retryable=False,
                evidence=evidence,
                duration_ms=duration_ms,
                exit_code=code,
                execution_method=method,
                summary=f"命令失败 exit_code={code}",
                observed_at=observed,
            )
        )

    # R01：先尊重结构化业务字段，再走文本启发式
    structured_status = _structured_business_status(parsed) if parsed else None
    if structured_status == "failed":
        err_msg = ""
        if parsed:
            nested = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
            err_msg = str(
                parsed.get("message")
                or parsed.get("error")
                or nested.get("message")
                or nested.get("error")
                or ""
            )
        return _with_meta(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data=structured_data,
                error_code="business_rejected",
                retryable=False,
                evidence=evidence,
                duration_ms=duration_ms,
                exit_code=code,
                execution_method=method,
                summary=(err_msg or "业务失败，不得记为成功")[:500],
                observed_at=observed,
            )
        )
    if structured_status in {"cancelled", "skipped", "unknown", "needs_confirmation"}:
        return _with_meta(
            ToolResult(
                status=structured_status,
                operation_id=op_id,
                data=structured_data,
                evidence=evidence,
                duration_ms=duration_ms,
                exit_code=code,
                execution_method=method,
                summary=structured_status,
                observed_at=observed,
            )
        )

    if _TIMEOUT_RE.search(raw) and (
        "错误" in raw or "Timeout" in raw or "timeout" in raw.lower()
    ):
        return _with_meta(
            ToolResult(
                status="unknown",
                operation_id=op_id,
                data=structured_data,
                error_code="timeout_unknown",
                retryable=True,
                evidence=evidence,
                duration_ms=duration_ms,
                exit_code=code,
                execution_method=method,
                summary="超时，执行结果未知，禁止推断未执行",
                observed_at=observed,
            )
        )

    if raw.startswith("错误:") or raw.startswith("错误："):
        return _with_meta(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data=structured_data,
                error_code="execute_error",
                retryable=False,
                evidence=evidence,
                duration_ms=duration_ms,
                exit_code=code,
                execution_method=method,
                summary=raw[:300],
                observed_at=observed,
            )
        )

    # R02：明确 success 时不把「已存在」当失败；文本拒绝仅作兜底
    if structured_status != "succeeded":
        if _BUSINESS_REJECT_RE.search(raw) or _EXISTS_REJECT_RE.search(raw):
            return _with_meta(
                ToolResult(
                    status="failed",
                    operation_id=op_id,
                    data=structured_data,
                    error_code="business_rejected",
                    retryable=False,
                    evidence=evidence,
                    duration_ms=duration_ms,
                    exit_code=code,
                    execution_method=method,
                    summary="业务拒绝，不得记为成功",
                    observed_at=observed,
                )
            )

    return _with_meta(
        ToolResult(
            status="succeeded",
            operation_id=op_id,
            data=structured_data,
            evidence=evidence,
            duration_ms=duration_ms,
            exit_code=0 if code is None else code,
            execution_method=method,
            summary=(raw[:280] + ("…" if len(raw) > 280 else "")),
            observed_at=observed,
        )
    )


def adapt_mcp_tool_result(
    content: Any,
    *,
    tool_name: str = "",
    duration_ms: float = 0.0,
) -> ToolResult:
    """将 MCP / 检索工具原始出口适配为统一 ToolResult（FR01.1）。

    覆盖：
    - MCP content 列表（[{type:text,text:...}]）
    - 已是 ToolResult dict
    - JSON / 纯文本业务结果
    """
    raw: Any = content
    if isinstance(raw, list):
        texts: List[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                texts.append(item)
            else:
                try:
                    texts.append(json.dumps(item, ensure_ascii=False))
                except (TypeError, ValueError):
                    texts.append(str(item))
        raw = "\n".join(texts) if texts else json.dumps(raw, ensure_ascii=False)

    if isinstance(raw, dict) and raw.get("status") in {
        "succeeded",
        "failed",
        "unknown",
        "needs_confirmation",
        "skipped",
        "cancelled",
    }:
        # 已是 ToolResult 形态
        return ToolResult(
            status=str(raw.get("status")),
            operation_id=str(raw.get("operation_id") or new_operation_id()),
            data=dict(raw.get("data") or {}),
            error_code=str(raw.get("error_code") or ""),
            retryable=bool(raw.get("retryable", False)),
            evidence=dict(raw.get("evidence") or {}),
            duration_ms=float(raw.get("duration_ms") or duration_ms or 0.0),
            exit_code=raw.get("exit_code"),
            output_ref=str(raw.get("output_ref") or ""),
            execution_method=str(raw.get("execution_method") or ""),
            resource_type=str(raw.get("resource_type") or ""),
            resource_id=str(raw.get("resource_id") or ""),
            source_url=str(raw.get("source_url") or ""),
            observed_at=str(raw.get("observed_at") or ""),
            summary=str(raw.get("summary") or "")[:500],
        )

    if isinstance(raw, (dict, list, int, float, bool)):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw or "")

    result = classify_skill_output(
        text=text,
        duration_ms=duration_ms,
        skill_name=tool_name or "mcp",
        command=f"mcp:{tool_name}",
    )
    result.evidence = dict(result.evidence or {})
    result.evidence["adapter"] = "mcp"
    result.evidence["tool_name"] = tool_name or ""
    return result


def summarize_batch_statuses(results: List[ToolResult]) -> Dict[str, Any]:
    counts = {
        "succeeded": 0,
        "failed": 0,
        "unknown": 0,
        "skipped": 0,
        "cancelled": 0,
        "needs_confirmation": 0,
    }
    for r in results:
        if r.status in counts:
            counts[r.status] += 1
        elif r.status == "succeeded":
            counts["succeeded"] += 1
    # 含 skipped/failed/unknown/cancelled/待确认 均不得报整批成功
    batch_ok = (
        counts["failed"] == 0
        and counts["unknown"] == 0
        and counts["skipped"] == 0
        and counts["cancelled"] == 0
        and counts["needs_confirmation"] == 0
        and counts["succeeded"] == len(results)
        and len(results) > 0
    )
    return {
        "ok": batch_ok,
        "counts": counts,
        "total": len(results),
        "message": (
            "整批成功"
            if batch_ok
            else "批次含失败/未知/跳过/待确认，不得报告整批成功"
        ),
    }
