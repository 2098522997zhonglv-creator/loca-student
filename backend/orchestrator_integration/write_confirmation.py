"""
写库确认（按钮 HITL）

execute_skill_script 返回 needs_confirmation 时，主流发出与 HITL 同形的 interrupt SSE，
前端复用 ToolApprovalCard；用户点允许/拒绝后走 resume，不再要求打字「确认写入」。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WRITE_CONFIRM_PREFIX = "write_confirm_"
_PENDING_TTL_SECONDS = 1800


class WriteConfirmStore:
    """线程安全的待写入确认缓存（进程内）。"""

    def __init__(self, ttl_seconds: int = _PENDING_TTL_SECONDS):
        self._items: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [k for k, (ts, _) in self._items.items() if now - ts > self._ttl]
        for k in expired:
            del self._items[k]

    def put(self, interrupt_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._purge_locked()
            self._items[interrupt_id] = (time.time(), payload)

    def pop(self, interrupt_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            entry = self._items.pop(interrupt_id, None)
            return entry[1] if entry else None

    def get(self, interrupt_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            entry = self._items.get(interrupt_id)
            return entry[1] if entry else None


_store: Optional[WriteConfirmStore] = None
_store_lock = threading.Lock()


def get_write_confirm_store() -> WriteConfirmStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = WriteConfirmStore()
    return _store


def is_write_confirm_interrupt(interrupt_id: Optional[str]) -> bool:
    return bool(interrupt_id) and str(interrupt_id).startswith(WRITE_CONFIRM_PREFIX)


def parse_needs_confirmation(content: Any) -> Optional[Dict[str, Any]]:
    """从工具输出解析 needs_confirmation；失败返回 None。"""
    if content is None:
        return None
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if not text or "needs_confirmation" not in text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        # 偶发前后缀噪音：尝试截取首个 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    if data.get("status") != "needs_confirmation":
        return None
    return data


def _summarize_pending(pending: Optional[Dict[str, Any]]) -> str:
    if not isinstance(pending, dict):
        return "平台/数据库写入"
    commands = pending.get("commands")
    lines: List[str] = []
    if isinstance(commands, list) and commands:
        for item in commands[:8]:
            if not isinstance(item, dict):
                continue
            skill = item.get("skill_name") or "?"
            cmd = str(item.get("command") or "").strip()
            if len(cmd) > 120:
                cmd = cmd[:117] + "..."
            lines.append(f"{skill}: {cmd}" if cmd else str(skill))
        if len(commands) > 8:
            lines.append(f"…另有 {len(commands) - 8} 条")
        return "；".join(lines) if lines else f"{len(commands)} 条写入命令"
    skill = pending.get("skill_name") or ""
    cmd = str(pending.get("command") or "").strip()
    if cmd and len(cmd) > 160:
        cmd = cmd[:157] + "..."
    if skill and cmd:
        return f"{skill}: {cmd}"
    return cmd or skill or "平台/数据库写入"


def build_write_confirm_interrupt(
    *,
    session_id: str,
    thread_id: str,
    tool_name: str,
    confirmation: Dict[str, Any],
) -> Dict[str, Any]:
    """缓存 pending 并构造 interrupt SSE 载荷。"""
    interrupt_id = f"{WRITE_CONFIRM_PREFIX}{uuid.uuid4().hex}"
    pending = confirmation.get("pending") if isinstance(confirmation.get("pending"), dict) else {}
    summary = _summarize_pending(pending)
    mode = confirmation.get("mode") or "confirm"
    store_payload = {
        "session_id": session_id,
        "thread_id": thread_id,
        "tool_name": tool_name,
        "pending": pending,
        "mode": mode,
        "executed_readonly": confirmation.get("executed_readonly") or [],
        "raw_message": confirmation.get("message") or "",
    }
    get_write_confirm_store().put(interrupt_id, store_payload)
    logger.info(
        "WriteConfirm: stored interrupt_id=%s session_id=%s tool=%s summary=%s",
        interrupt_id,
        session_id,
        tool_name,
        summary[:200],
    )
    title = "确认写入（草稿）" if mode == "draft" else "确认写入"
    return {
        "type": "interrupt",
        "interrupt_id": interrupt_id,
        "kind": "write_confirmation",
        "action_requests": [
            {
                "name": title,
                "args": {
                    "tool": tool_name or "execute_skill_script",
                    "summary": summary,
                    "mode": mode,
                    "pending": pending,
                },
                "description": (
                    "将执行平台/数据库写入，请确认后继续。"
                    if mode != "draft"
                    else "草稿态：确认后生成待落库变更（仍不直接写库，除非后续正式确认）。"
                ),
            }
        ],
        "session_id": session_id,
        "thread_id": thread_id,
    }


def build_resume_user_message(
    decision: str, pending_payload: Optional[Dict[str, Any]]
) -> str:
    """构造 resume 时注入的用户消息（可被 detect_user_write_approval 识别）。"""
    pending = (pending_payload or {}).get("pending") if pending_payload else None
    summary = _summarize_pending(pending if isinstance(pending, dict) else None)
    if decision == "approve":
        body = (
            "确认写入。界面按钮已同意本次写入，请立即用 execute_skill_script "
            "以 user_confirmed=true 执行下列待写入命令，不要再询问确认：\n"
            f"{summary}"
        )
        if isinstance(pending, dict):
            try:
                body += "\n\npending_json=\n" + json.dumps(
                    pending, ensure_ascii=False, indent=2
                )
            except (TypeError, ValueError):
                pass
        return body
    return (
        "取消写入。界面按钮已拒绝本次写入，请确认已取消，不要执行任何写入命令。"
        f"原计划：{summary}"
    )
