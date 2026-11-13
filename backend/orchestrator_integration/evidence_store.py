"""证据存取（FR01.4 / FR09.2 / RC16）。

原始工具输出按 EvidenceRecord 持久化，经 output_ref/evidence_id 授权分页读取。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*['\"]?[^\s'\"]+"
)
_INLINE_LIMIT = 32_000
_PAGE_DEFAULT = 4000
_PAGE_MAX = 16_000


def _evidence_root() -> Path:
    root = Path(
        os.environ.get(
            "LOCA_STUDE_EVIDENCE_DIR",
            str(Path(__file__).resolve().parents[2] / "data" / "evidence"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _redact_text(text: str) -> str:
    return _SECRET_RE.sub(r"\1=[REDACTED]", text or "")


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def store_tool_output_evidence(
    task,
    *,
    step=None,
    tool_name: str = "",
    summary: str = "",
    full_text: str = "",
    structured: Optional[Dict[str, Any]] = None,
    claim_type: str = "observation",
) -> Dict[str, Any]:
    """写入 EvidenceRecord，返回 output_ref / evidence_id。"""
    from orchestrator_integration.models import EvidenceRecord

    structured = structured or {}
    raw = _redact_text(full_text or summary or "")
    digest = content_hash(raw)
    observed_at = timezone.now()
    storage_ref = ""
    observed: Dict[str, Any] = {
        "tool_name": tool_name or "",
        "summary": (summary or "")[:2000],
        "content_hash": digest,
        "bytes": len(raw.encode("utf-8")),
        "status": structured.get("status") or "",
        "resource_type": structured.get("resource_type") or "",
        "resource_id": structured.get("resource_id") or "",
        "source_url": structured.get("source_url") or "",
        "execution_method": structured.get("execution_method") or "",
    }

    if len(raw) <= _INLINE_LIMIT:
        observed["full_text"] = raw
        storage_ref = f"inline:{digest[:16]}"
    else:
        task_key = str(getattr(task, "public_task_id", None) or task.pk)
        op = str(structured.get("operation_id") or digest[:12])
        path = _evidence_root() / task_key / f"{op}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        storage_ref = f"file:{path}"
        observed["full_text_preview"] = raw[:1500] + f"\n…[共 {len(raw)} 字符，见分页读取]"

    row = EvidenceRecord.objects.create(
        task=task,
        step=step,
        evidence_type="tool_output",
        resource_ref=str(
            structured.get("resource_id")
            or structured.get("operation_id")
            or ""
        )[:255],
        resource_version=digest[:16],
        observed_at=observed_at,
        validator="evidence_store",
        expected={},
        observed=observed,
        claim_type=claim_type or "observation",
        storage_ref=storage_ref[:255],
    )
    output_ref = f"evidence:{row.id}"
    # 回写便于 ToolResult / step 引用
    observed["output_ref"] = output_ref
    EvidenceRecord.objects.filter(pk=row.pk).update(observed=observed)
    return {
        "ok": True,
        "evidence_id": row.id,
        "output_ref": output_ref,
        "content_hash": digest,
        "observed_at": observed_at.isoformat(),
        "storage_ref": storage_ref,
    }


def _load_full_text(row) -> Tuple[str, Optional[str]]:
    """返回 (text, error)。"""
    observed = row.observed if isinstance(row.observed, dict) else {}
    if isinstance(observed.get("full_text"), str):
        return observed["full_text"], None
    ref = str(row.storage_ref or "")
    if ref.startswith("file:"):
        path = Path(ref[5:])
        if not path.is_file():
            return "", "expired_or_missing"
        try:
            return path.read_text(encoding="utf-8"), None
        except OSError as exc:
            return "", f"read_error:{exc}"
    if isinstance(observed.get("full_text_preview"), str):
        return observed["full_text_preview"], "preview_only"
    return "", "empty"


def assert_evidence_access(
    row, *, user_id: int, project_id: Optional[int] = None
) -> Dict[str, Any]:
    from orchestrator_integration.agent_runtime import assert_task_access

    task = row.task
    return assert_task_access(
        task, user_id=user_id, project_id=project_id, session_id=""
    )


def read_evidence(
    *,
    evidence_id: Optional[int] = None,
    output_ref: str = "",
    user_id: int,
    project_id: Optional[int] = None,
    offset: int = 0,
    limit: int = _PAGE_DEFAULT,
) -> Dict[str, Any]:
    """授权分页读取证据正文（RC16）。"""
    from orchestrator_integration.models import EvidenceRecord

    row = None
    if evidence_id:
        row = EvidenceRecord.objects.select_related("task").filter(pk=evidence_id).first()
    elif output_ref:
        ref = (output_ref or "").strip()
        if ref.startswith("evidence:"):
            try:
                eid = int(ref.split(":", 1)[1])
            except (TypeError, ValueError):
                return {"ok": False, "error_code": "invalid_output_ref", "status": 400}
            row = EvidenceRecord.objects.select_related("task").filter(pk=eid).first()
        else:
            row = (
                EvidenceRecord.objects.select_related("task")
                .filter(storage_ref=ref)
                .order_by("-created_at")
                .first()
            )
    if row is None:
        return {"ok": False, "error_code": "not_found", "status": 404}

    access = assert_evidence_access(row, user_id=user_id, project_id=project_id)
    if not access.get("ok"):
        return {
            "ok": False,
            "error_code": access.get("error_code") or "permission_denied",
            "status": 403,
        }

    text, load_err = _load_full_text(row)
    if load_err == "expired_or_missing":
        return {
            "ok": False,
            "error_code": "expired",
            "status": 410,
            "message": "证据文件不可用或已过期",
        }

    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or _PAGE_DEFAULT), _PAGE_MAX))
    chunk = text[offset : offset + limit]
    observed = row.observed if isinstance(row.observed, dict) else {}
    return {
        "ok": True,
        "evidence_id": row.id,
        "output_ref": f"evidence:{row.id}",
        "evidence_type": row.evidence_type,
        "claim_type": row.claim_type,
        "content_type": "text/plain; charset=utf-8",
        "content_hash": observed.get("content_hash") or content_hash(text),
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        "resource_scope": {
            "task_id": row.task.public_task_id or str(row.task_id),
            "project_id": row.task.project_id,
            "resource_ref": row.resource_ref,
        },
        "offset": offset,
        "limit": limit,
        "total_chars": len(text),
        "has_more": offset + limit < len(text),
        "next_offset": offset + limit if offset + limit < len(text) else None,
        "chunk": chunk,
        "summary": observed.get("summary") or "",
        "load_warning": load_err or "",
    }
