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


def _coerce_needs_confirmation_dict(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """归一化为顶层含 pending 的 needs_confirmation（兼容 ToolResult 包装）。

    FR05.3 / D01：adapt 后形态为
    {status, data:{status,pending,...}, evidence:{pending}}，
    若仍取顶层 pending 会得到 {}，导致批准后无法恢复原动作。
    """
    if not isinstance(data, dict):
        return None
    if data.get("status") != "needs_confirmation":
        return None

    pending = data.get("pending")
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}

    if not isinstance(pending, dict) or not pending:
        for candidate in (
            nested.get("pending") if isinstance(nested, dict) else None,
            evidence.get("pending"),
        ):
            if isinstance(candidate, dict) and candidate:
                pending = candidate
                break
        else:
            pending = pending if isinstance(pending, dict) else {}

    mode = data.get("mode") or nested.get("mode") or "confirm"
    message = data.get("message") or nested.get("message") or data.get("summary") or ""
    executed_readonly = (
        data.get("executed_readonly")
        if isinstance(data.get("executed_readonly"), list)
        else nested.get("executed_readonly")
        if isinstance(nested.get("executed_readonly"), list)
        else []
    )
    return {
        "status": "needs_confirmation",
        "mode": mode,
        "message": message,
        "pending": pending if isinstance(pending, dict) else {},
        "executed_readonly": executed_readonly or [],
    }


def parse_needs_confirmation(content: Any) -> Optional[Dict[str, Any]]:
    """从工具输出解析 needs_confirmation；失败返回 None。"""
    if content is None:
        return None
    if isinstance(content, dict):
        return _coerce_needs_confirmation_dict(content)
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
    return _coerce_needs_confirmation_dict(data)


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
    # FR05.3：同步持久化审批（失败不影响内存路径）
    try:
        _persist_approval_request(
            interrupt_id=interrupt_id,
            session_id=session_id,
            tool_name=tool_name or "execute_skill_script",
            pending=pending,
            summary=summary,
            mode=mode,
        )
    except Exception as exc:
        logger.warning("WriteConfirm: persist ApprovalRequest failed: %s", exc)
    logger.info(
        "WriteConfirm: stored interrupt_id=%s session_id=%s tool=%s summary=%s",
        interrupt_id,
        session_id,
        tool_name,
        summary[:200],
    )
    title = "确认写入（草稿）" if mode == "draft" else "确认写入"
    # RC07 / 交付物：前端须展示原动作（技能+命令），批准后只执行该快照
    cmds = normalize_pending_commands(pending if isinstance(pending, dict) else {})
    if cmds:
        original_action = "；".join(
            f"{c.get('skill_name')}: {c.get('command')}" for c in cmds
        )
    elif isinstance(pending, dict):
        original_action = str(
            pending.get("command") or pending.get("summary") or summary or ""
        )
    else:
        original_action = summary or ""
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
                    "original_action": original_action[:2000],
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


def _persist_approval_request(
    *,
    interrupt_id: str,
    session_id: str,
    tool_name: str,
    pending: Dict[str, Any],
    summary: str,
    mode: str,
) -> None:
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from orchestrator_integration.agent_runtime import fingerprint_payload
    from orchestrator_integration.models import AgentTask, ApprovalRequest
    from langgraph_integration.models import ChatSession

    session = ChatSession.objects.filter(session_id=session_id).select_related("user", "project").first()
    if session is None or session.user_id is None:
        return
    task = (
        AgentTask.objects.filter(session=session)
        .exclude(status__in=("completed", "failed", "cancelled"))
        .order_by("-updated_at")
        .first()
    )
    ApprovalRequest.objects.update_or_create(
        interrupt_id=interrupt_id,
        defaults={
            "task": task,
            "user_id": session.user_id,
            "project_id": session.project_id,
            "session_id": session_id,
            "action_name": tool_name,
            "action_args": {"pending": pending, "mode": mode},
            "param_fingerprint": fingerprint_payload(pending),
            "kind": "write_confirmation",
            "status": "pending",
            "expires_at": timezone.now() + timedelta(minutes=30),
            "summary": summary[:2000],
        },
    )


def consume_approval(
    interrupt_id: str,
    *,
    user_id: int,
    decision: str,
    expected_fingerprint: str = "",
    project_id: Optional[int] = None,
    session_id: str = "",
) -> Dict[str, Any]:
    """精确消费审批（FR05.4）：条件更新，防重复/过期/参数漂移/跨项目。"""
    from django.db import transaction
    from django.utils import timezone

    from orchestrator_integration.models import ApprovalRequest

    with transaction.atomic():
        row = (
            ApprovalRequest.objects.select_for_update()
            .filter(interrupt_id=interrupt_id)
            .first()
        )
        if row is None:
            # 兼容仅内存缓存的旧路径
            return {"ok": True, "mode": "memory_only", "decision": decision}
        if row.user_id != user_id:
            return {"ok": False, "error_code": "permission_denied"}
        if (
            project_id is not None
            and row.project_id is not None
            and int(row.project_id) != int(project_id)
        ):
            return {"ok": False, "error_code": "project_mismatch"}
        if session_id and row.session_id and row.session_id != session_id:
            return {"ok": False, "error_code": "session_mismatch"}
        if row.status in ("consumed", "rejected", "expired", "cancelled"):
            return {
                "ok": False,
                "error_code": f"approval_{row.status}",
                "status": row.status,
            }
        if row.expires_at and row.expires_at <= timezone.now():
            row.status = "expired"
            row.save(update_fields=["status", "updated_at"])
            return {"ok": False, "error_code": "approval_expired"}
        # R31：每次消费重新计算保存参数指纹，并与批准版本比较
        from orchestrator_integration.agent_runtime import fingerprint_payload

        if row.param_fingerprint and isinstance(row.action_args, dict):
            candidates = {
                fingerprint_payload(row.action_args),
            }
            if "pending" in row.action_args:
                candidates.add(fingerprint_payload(row.action_args.get("pending")))
            # 允许历史落库：指纹可能针对 pending 或整包 action_args
            if candidates and row.param_fingerprint not in candidates:
                return {"ok": False, "error_code": "param_fingerprint_mismatch"}

        fp = expected_fingerprint
        if not fp:
            mem = get_write_confirm_store().get(interrupt_id)
            if isinstance(mem, dict) and mem.get("pending") is not None:
                fp = fingerprint_payload(mem.get("pending"))
        if fp and row.param_fingerprint and fp != row.param_fingerprint:
            return {"ok": False, "error_code": "param_fingerprint_mismatch"}
        row.decision = decision
        if decision == "approve":
            row.status = "consumed"
            row.consumed_at = timezone.now()
        else:
            row.status = "rejected"
            row.consumed_at = timezone.now()
        row.save(
            update_fields=["decision", "status", "consumed_at", "updated_at"]
        )
        return {
            "ok": True,
            "status": row.status,
            "decision": decision,
            "action_args": row.action_args,
            "param_fingerprint": row.param_fingerprint,
            "task_id": row.task_id,
            "step_id": row.step_id,
        }


def normalize_pending_commands(pending: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 pending 归一成 [{skill_name, command, session_id?}, ...]。"""
    if not isinstance(pending, dict):
        return []
    commands = pending.get("commands")
    out: List[Dict[str, Any]] = []
    if isinstance(commands, list) and commands:
        for item in commands:
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill_name") or "").strip()
            cmd = str(item.get("command") or "").strip()
            if not skill or not cmd:
                continue
            row = {"skill_name": skill, "command": cmd}
            if item.get("session_id"):
                row["session_id"] = str(item.get("session_id"))
            if item.get("step_id"):
                row["step_id"] = str(item.get("step_id"))
            out.append(row)
        return out
    skill = str(pending.get("skill_name") or "").strip()
    cmd = str(pending.get("command") or "").strip()
    if skill and cmd:
        return [{"skill_name": skill, "command": cmd}]
    return []


def execute_approved_pending(
    pending: Optional[Dict[str, Any]],
    *,
    user_id: int,
    project_id: int = 0,
    chat_session_id: str = "",
) -> Dict[str, Any]:
    """FR05.4 / RC07：服务端精确执行已批准的原动作一次，不依赖模型自批。

    使用 user_write_approved=True 的闭包仅跑 pending 内命令；不开放整轮任意写入。
    """
    cmds = normalize_pending_commands(pending)
    if not cmds:
        return {
            "ok": False,
            "error_code": "empty_pending",
            "message": "审批记录中缺少可执行的 pending 命令",
            "results": [],
        }

    from orchestrator_integration.builtin_tools import get_builtin_tools

    tools = get_builtin_tools(
        user_id=int(user_id),
        project_id=int(project_id or 0),
        chat_session_id=chat_session_id or None,
        user_write_approved=True,
        allow_execute=True,
        include_meta_tools=False,
    )
    execute_tool = next(
        (t for t in tools if getattr(t, "name", "") == "execute_skill_script"),
        None,
    )
    if execute_tool is None:
        return {
            "ok": False,
            "error_code": "execute_tool_missing",
            "message": "未能挂载 execute_skill_script，无法执行已批准写入",
            "results": [],
        }

    results: List[Dict[str, Any]] = []
    all_ok = True
    invoke = getattr(execute_tool, "invoke", None) or getattr(execute_tool, "run", None)
    if invoke is None:
        return {
            "ok": False,
            "error_code": "execute_tool_not_callable",
            "message": "execute_skill_script 不可调用",
            "results": [],
        }

    # 批量一次调用，保留依赖顺序
    payload = {
        "commands": cmds,
        "parallel": False,
        "user_confirmed": True,
    }
    try:
        raw = invoke(payload)
        text = raw if isinstance(raw, str) else str(raw)
        # 若仍返回 needs_confirmation，视为失败（不应发生）
        if parse_needs_confirmation(text) is not None:
            all_ok = False
        results.append(
            {
                "commands": cmds,
                "output": text,
            }
        )
    except Exception as exc:
        logger.exception("execute_approved_pending failed: %s", exc)
        all_ok = False
        results.append({"commands": cmds, "error": str(exc)})

    try:
        from orchestrator_integration.agent_runtime import fingerprint_payload

        fp = fingerprint_payload(pending if isinstance(pending, dict) else {})
    except Exception:
        fp = ""

    return {
        "ok": all_ok,
        "error_code": "" if all_ok else "approved_execute_failed",
        "message": "已批准动作已由服务端执行" if all_ok else "已批准动作执行失败",
        "results": results,
        "pending_fingerprint": fp,
    }


def build_resume_user_message(
    decision: str,
    pending_payload: Optional[Dict[str, Any]],
    *,
    server_executed: Optional[Dict[str, Any]] = None,
) -> str:
    """构造 resume 时注入的用户消息。

    批准且服务端已执行时：只要求模型汇总，禁止再次发起写入。
    """
    pending = (pending_payload or {}).get("pending") if pending_payload else None
    summary = _summarize_pending(pending if isinstance(pending, dict) else None)
    if decision == "approve":
        if isinstance(server_executed, dict) and server_executed.get("ok"):
            outputs = []
            for item in server_executed.get("results") or []:
                if isinstance(item, dict) and item.get("output"):
                    outputs.append(str(item["output"]))
            joined = "\n\n".join(outputs) if outputs else "(无输出摘要)"
            return (
                "确认写入。界面按钮已同意；服务端已精确执行下列原动作一次，"
                "请用中文汇总交付结果与证据，禁止再次调用写入类 execute_skill_script：\n"
                f"{summary}\n\n执行结果：\n{joined}"
            )
        if isinstance(server_executed, dict) and server_executed.get("error_code"):
            return (
                "确认写入。界面已同意，但服务端执行已批准动作失败"
                f"（{server_executed.get('error_code')}）："
                f"{server_executed.get('message') or ''}。"
                "请报告该阻塞，不要重新编造写入命令，也不要要求用户再说继续。"
            )
        # 兼容旧路径：仍提示模型带 pending 执行（不应作为主路径）
        body = (
            "确认写入。界面按钮已同意本次写入；服务端已按原授权重挂 execute_skill_script。"
            "请立即以 user_confirmed=true 执行下列待写入命令，不要再询问确认；"
            "禁止要求用户「放行工具 / 重新授权路由 / 再说继续」：\n"
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
