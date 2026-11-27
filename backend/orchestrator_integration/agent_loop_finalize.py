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
    final_response: str = ""
    executor_mounted: bool = False

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
    # 即便未推进，编排路径也可能已绑定 execution_ref / 写回 facts
    try:
        from orchestrator_integration.task_orchestrator.node_runtime import (
            apply_remount_to_context,
        )

        if adv.get("remount_skills"):
            apply_remount_to_context(adv.get("remount_skills") or [])
    except Exception:
        pass
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
    from .final_response_policy import requests_user_to_remount_executor

    if requests_user_to_remount_executor(session.final_response):
        if task is not None:
            try:
                task.refresh_from_db(fields=["budget"])
            except Exception:
                pass
        budget = dict(getattr(task, "budget", None) or {}) if task is not None else {}
        used_tools = int(
            budget.get("used_tool_actions") or budget.get("used_tool_steps") or 0
        )
        max_tools = int(
            budget.get("max_tool_actions") or budget.get("max_tool_steps") or 0
        )
        completed_business = int(budget.get("completed_business_steps") or 0)
        expected_business = int(budget.get("expected_business_steps") or 0)
        exhausted_without_progress = (
            session.executor_mounted
            and max_tools > 0
            and used_tools >= max_tools
            and expected_business > completed_business
        )
        if exhausted_without_progress:
            blocker_code = "execution_budget_exhausted_no_progress"
            reason = (
                f"工具调用预算已耗尽（{used_tools}/{max_tools}），"
                f"业务检查点完成 {completed_business}/{expected_business}；"
                "任务未完成，需排查工具循环并恢复执行。"
            )
        else:
            blocker_code = "assistant_deferred_executor_mount"
            reason = "执行能力未就绪，本轮未完成业务执行；任务保持未完成，由服务端恢复执行。"
        acceptance = {
            "ok": False,
            "reasons": [reason],
            "blockers": [
                {
                    "blocker_code": blocker_code,
                    "step": "autonomy",
                }
            ],
        }
        if task is not None:
            persisted = session.runtime.accept(acceptance)
            events.append(
                {
                    "type": persisted.get("type") or "task_blocked",
                    "task_id": session.task_id,
                    "status": persisted.get("status") or "blocked",
                    "acceptance": acceptance,
                }
            )
        else:
            events.append(
                {
                    "type": "task_blocked",
                    "task_id": session.task_id,
                    "status": "blocked",
                    "acceptance": acceptance,
                }
            )
        return acceptance, events
    if task is not None:
        from .browser_test_runtime import delivered_acceptance
        task.refresh_from_db()
        delivered = delivered_acceptance(task)
        if delivered:
            persisted = session.runtime.accept(delivered)
            from .runtime_progress import progress_event
            events.append(progress_event(task.pk))
            events.append({"type": "task_completed", "task_id": session.task_id, "status": "completed",
                           "acceptance": delivered, "event_id": persisted.get("event_id")})
            return delivered, events
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
            # 多阶段未到末节点：流结束由 close_task_after_stream 收口，
            # 此处只推送阶段信息，不再假装可长期停在 running。
            evt_payload = {
                "type": "stage_changed",
                "task_id": session.task_id,
                "status": "running",
                "payload": persisted.get("payload") or {},
                "deferred_completion": True,
            }
            acceptance = dict(acceptance)
            acceptance["ok"] = False
            acceptance["deferred_completion"] = True
            acceptance["reasons"] = list(acceptance.get("reasons") or []) + [
                str(
                    (persisted.get("payload") or {}).get("reason")
                    or "not_last_stage_defer_completion"
                )
            ]
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
