"""Agent Loop 收尾编排（从 agent_loop_view 抽离）。

负责：探测提示、阶段推进、强制验收与 SSE 载荷构造。
流式 token 仍由 view 推送；任务态一律经 AgentRuntime。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from orchestrator_integration.agent_runtime import (
    AgentRuntime,
    format_probe_hint,
    probe_browser_resources,
)
from orchestrator_integration.execution_policy import evaluate_post_condition
from orchestrator_integration.telemetry_context import (
    get_telemetry_context,
    set_telemetry_context,
)


@dataclass
class RuntimeSession:
    """单轮对话的运行时上下文（首次执行与 resume 共用）。"""

    runtime: AgentRuntime
    commission: Any = None
    stage_specs: List[Any] = field(default_factory=list)
    auth_scope: Dict[str, Any] = field(default_factory=dict)

    @property
    def task(self):
        return self.runtime.task

    @property
    def task_id(self) -> str:
        t = self.task
        if t is None:
            return ""
        return t.public_task_id or str(t.id)


def build_message_probe(user_message: str, *, target_url: str = "") -> Dict[str, Any]:
    """从用户话术提取 session/file/url 并探测。"""
    text = user_message or ""
    sid_m = re.search(r"session[_-]?id[=:\s]+([A-Za-z0-9_-]+)", text, re.I)
    path_m = re.search(r"(/[^\s]+\.(?:png|jpe?g|webp|gif|pdf))", text, re.I)
    url_m = re.search(r"https?://[^\s]+", text, re.I)
    url = target_url or (url_m.group(0) if url_m else "")
    if not any(
        k in text for k in ("继续", "复用", "session_id", "接着")
    ) and not (sid_m or path_m):
        # 无复用语义时仍可对目标 URL 做域名探测
        if not url:
            return {}
    probe = probe_browser_resources(
        session_id=sid_m.group(1) if sid_m else "",
        file_path=path_m.group(1) if path_m else "",
        target_url=url,
    )
    return probe


def probe_hint_for_message(user_message: str, *, target_url: str = "") -> str:
    probe = build_message_probe(user_message, target_url=target_url)
    return format_probe_hint(probe) if probe else ""


def maybe_advance_and_events(
    session: RuntimeSession, tool_results: List[str]
) -> List[Dict[str, Any]]:
    """尝试阶段推进，返回待推送 SSE 事件列表。"""
    events: List[Dict[str, Any]] = []
    if session.runtime.task is None:
        return events
    adv = session.runtime.maybe_advance(tool_results)
    if not adv.get("advanced"):
        return events
    events.append(
        {
            "type": "stage_changed",
            "task_id": session.task_id,
            "status": "running",
            "payload": adv,
        }
    )
    try:
        ctx = get_telemetry_context()
        ctx.current_stage = adv.get("current_stage") or ctx.current_stage
        set_telemetry_context(ctx)
    except Exception:
        pass
    return events


def finalize_task_acceptance(
    session: RuntimeSession,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """强制验收 + 经 Runtime 落库；返回 (acceptance, sse_events)。"""
    commission = session.commission
    task = session.task
    events: List[Dict[str, Any]] = []
    if commission is None:
        return {"ok": False, "reasons": ["no_commission"]}, events

    tool_results = list(getattr(commission, "last_tool_results", None) or [])
    events.extend(maybe_advance_and_events(session, tool_results))

    auth_scope = {}
    target_url = ""
    if task is not None:
        auth_scope = getattr(task, "auth_scope", None) or session.auth_scope or {}
        target_url = str((getattr(task, "task_scope", None) or {}).get("target_url") or "")

    playbook_id = getattr(commission, "playbook_id", "") or ""
    # 持久化阶段优先：浏览器任务不得被本轮 QA 立项跳过证据门禁（FR03.2）
    cur_stage = str(getattr(task, "current_stage", "") or "") if task else ""
    allow_actions = set((auth_scope or {}).get("allow_actions") or [])
    browser_persist = cur_stage == "browser_interact" or bool(
        allow_actions & {"upload", "add_to_cart", "submit_order", "checkout", "pay"}
    )
    if browser_persist and playbook_id != "browser_act":
        playbook_id = "browser_act"

    facts = dict(getattr(task, "facts", None) or {}) if task is not None else {}
    stage_plan = list(getattr(task, "stage_plan", None) or []) if task is not None else []
    acceptance = evaluate_post_condition(
        post_condition=getattr(commission, "post_condition", "") or "",
        tool_results=tool_results,
        had_unconfirmed_write=False,
        playbook_id=playbook_id,
        auth_scope=auth_scope,
        target_url=target_url,
        stage_plan=stage_plan,
        facts=facts,
        evidence_epoch=facts.get("evidence_epoch"),
    )
    # 供 Runtime.accept 绑定末阶段证据（R11）
    acceptance = dict(acceptance)
    acceptance["tool_results"] = tool_results
    if browser_persist and acceptance.get("ok"):
        from orchestrator_integration.agent_runtime import stage_acceptance_heuristic

        verdict = stage_acceptance_heuristic(cur_stage or "browser_interact", tool_results)
        if not verdict.get("ok"):
            acceptance["ok"] = False
            reasons = list(acceptance.get("reasons") or [])
            reasons.append(
                "原浏览器任务缺少页面动作/产物证据，禁止 completed（不得以用户重发结束）"
            )
            acceptance["reasons"] = reasons
            acceptance["stage_verdict"] = verdict

    evt_payload: Dict[str, Any] = {
        "type": "task_completed" if acceptance.get("ok") else "task_blocked",
        "task_id": session.task_id or getattr(commission, "task_id", ""),
        "status": "completed" if acceptance.get("ok") else "blocked",
        "acceptance": acceptance,
    }

    if session.runtime.task is not None:
        persisted = session.runtime.accept(acceptance)
        if persisted.get("deferred_completion"):
            evt_payload = {
                "type": "stage_changed",
                "task_id": session.task_id,
                "status": "running",
                "payload": persisted.get("payload") or {},
            }
        else:
            evt_payload.update(
                {
                    "event_id": persisted.get("event_id"),
                    "sequence": persisted.get("sequence"),
                    "type": persisted.get("type") or evt_payload["type"],
                    "status": persisted.get("status") or evt_payload["status"],
                }
            )
    events.append(evt_payload)
    return acceptance, events
