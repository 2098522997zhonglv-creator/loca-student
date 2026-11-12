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
_BUSINESS_REJECT_RE = re.compile(
    r"余额不足|库存不足|权限不足|无权限|已存在|不存在|状态不允许|业务校验|"
    r"business[_ ]?error|rejected by policy|permission denied",
    re.I,
)


def extract_typed_identifiers(text: str) -> Dict[str, str]:
    """从工具输出提取类型化业务标识（忽略 unknown/null）。"""
    found: Dict[str, str] = {}
    for m in _ID_FIELD_RE.finditer(text or ""):
        found[m.group(1).lower()] = m.group(2)
    return found


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

    # needs_confirmation JSON
    if "needs_confirmation" in raw:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, dict) and data.get("status") == "needs_confirmation":
                    return ToolResult(
                        status="needs_confirmation",
                        operation_id=op_id,
                        data=data,
                        error_code="needs_confirmation",
                        retryable=False,
                        evidence={"pending": data.get("pending")},
                        duration_ms=duration_ms,
                        summary=str(data.get("message") or "需要写入确认")[:500],
                        observed_at=observed,
                    )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    code = exit_code
    if code is None:
        m = _EXIT_CODE_RE.search(raw)
        if m:
            try:
                code = int(m.group(1) or m.group(2))
            except (TypeError, ValueError):
                code = None

    method = infer_execution_method(raw)
    ids = extract_typed_identifiers(raw)
    primary_type = next(iter(ids), "")
    primary_id = ids.get(primary_type, "") if primary_type else ""
    evidence: Dict[str, Any] = {
        "skill_name": skill_name or "",
        "command_preview": (command or "")[:240],
    }
    if method:
        evidence["execution_method"] = method
    if ids:
        evidence["identifiers"] = ids

    def _with_ids(tr: ToolResult) -> ToolResult:
        if primary_type:
            tr.resource_type = primary_type
            tr.resource_id = primary_id
        return tr

    if code is not None and code != 0:
        return _with_ids(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data={"raw_text": raw},
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

    if _TIMEOUT_RE.search(raw) and (
        "错误" in raw or "Timeout" in raw or "timeout" in raw.lower()
    ):
        # 超时可能已提交副作用 → unknown（FR01.2）
        return _with_ids(
            ToolResult(
                status="unknown",
                operation_id=op_id,
                data={"raw_text": raw},
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
        return _with_ids(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data={"raw_text": raw},
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

    if _BUSINESS_REJECT_RE.search(raw):
        return _with_ids(
            ToolResult(
                status="failed",
                operation_id=op_id,
                data={"raw_text": raw},
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

    return _with_ids(
        ToolResult(
            status="succeeded",
            operation_id=op_id,
            data={"raw_text": raw, "identifiers": ids} if ids else {"raw_text": raw},
            evidence=evidence,
            duration_ms=duration_ms,
            exit_code=0 if code is None else code,
            execution_method=method,
            summary=(raw[:280] + ("…" if len(raw) > 280 else "")),
            observed_at=observed,
        )
    )


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
