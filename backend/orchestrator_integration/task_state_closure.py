"""第 1 阶段：任务状态闭环。

目标：对话流结束后，任务不得无事件、无原因地停在 running。
统一状态：queued → running → waiting_approval / blocked / completed / failed / cancelled
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.utils import timezone

# 对外统一名；DB 仍兼容 pending / awaiting_approval
CANONICAL_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "blocked",
    "completed",
    "failed",
    "cancelled",
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
OPEN_STATUSES = frozenset({"queued", "pending", "running", "paused"})


def normalize_status(status: str) -> str:
    """映射历史别名到统一状态名。"""
    st = (status or "").strip().lower()
    if st in {"pending", "queued"}:
        return "queued"
    if st in {"awaiting_approval", "waiting_approval"}:
        return "waiting_approval"
    if st == "cancelling":
        return "cancelled"  # 展示层：取消中视为终态过程；库内可仍为 cancelling
    if st in CANONICAL_STATUSES or st == "cancelling":
        return st if st != "cancelling" else "cancelled"
    return st or "queued"


def budget_snapshot(task) -> Dict[str, int]:
    budget = dict(getattr(task, "budget", None) or {})
    return {
        "completed_business_steps": int(budget.get("completed_business_steps") or 0),
        "expected_business_steps": int(budget.get("expected_business_steps") or 0),
        "used_tool_actions": int(
            budget.get("used_tool_actions") or budget.get("used_tool_steps") or 0
        ),
        "max_tool_actions": int(
            budget.get("max_tool_actions") or budget.get("max_tool_steps") or 0
        ),
        "reserved_tool_actions": int(budget.get("reserved_tool_actions") or 0),
    }


def incomplete_checkpoints(task) -> Dict[str, Any]:
    """未完成的业务检查点（阶段 / 预算计数）。"""
    snap = budget_snapshot(task)
    expected = snap["expected_business_steps"]
    completed = snap["completed_business_steps"]
    stages = list(getattr(task, "stage_plan", None) or [])
    facts = dict(getattr(task, "facts", None) or {})
    raw_done = facts.get("completed_stage_ids") or []
    done_ids = set()
    for item in raw_done:
        if isinstance(item, dict):
            sid = item.get("stage_id") or item.get("id")
            if sid:
                done_ids.add(str(sid))
        elif item:
            done_ids.add(str(item))
    stage_ids = [
        str(s.get("stage_id") or "")
        for s in stages
        if isinstance(s, dict) and s.get("stage_id")
    ]
    remaining_stages = [sid for sid in stage_ids if sid and sid not in done_ids]
    incomplete = False
    reasons: List[str] = []
    if expected > 0 and completed < expected:
        incomplete = True
        reasons.append(f"业务检查点未完成 {completed}/{expected}")
    if len(stage_ids) > 1 and remaining_stages:
        incomplete = True
        reasons.append(
            "未完成阶段: " + ",".join(remaining_stages[:8])
        )
    return {
        "incomplete": incomplete,
        "reasons": reasons,
        "remaining_stages": remaining_stages,
        "budget": snap,
    }


def budget_remaining(task) -> bool:
    snap = budget_snapshot(task)
    max_a = snap["max_tool_actions"]
    if max_a <= 0:
        return True
    used = snap["used_tool_actions"] + snap["reserved_tool_actions"]
    return used < max_a


def _block_task(
    task,
    *,
    blocker_code: str,
    reasons: List[str],
    recovery: str,
    acceptance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from orchestrator_integration.agent_runtime import append_event, apply_acceptance

    acc = dict(acceptance or {})
    acc["ok"] = False
    acc_reasons = list(acc.get("reasons") or [])
    for r in reasons:
        if r and r not in acc_reasons:
            acc_reasons.append(r)
    acc["reasons"] = acc_reasons
    blockers = list(acc.get("blockers") or [])
    blockers.append(
        {
            "blocker_code": blocker_code,
            "recovery": recovery,
            "step": "stream_closure",
        }
    )
    acc["blockers"] = blockers
    acc["recovery"] = recovery
    acc["closure"] = True

    st = str(getattr(task, "status", "") or "")
    if st in TERMINAL_STATUSES or st == "cancelling":
        return {
            "action": "already_terminal",
            "status": normalize_status(st),
            "acceptance": acc,
        }

    if st == "awaiting_approval":
        return {
            "action": "keep_waiting_approval",
            "status": "waiting_approval",
            "acceptance": acc,
        }

    evt = apply_acceptance(task, acc)
    # 补充显式闭环事件，便于刷新后还原
    try:
        append_event(
            task,
            "stream_closure",
            status="blocked",
            payload={
                "blocker_code": blocker_code,
                "reasons": acc_reasons,
                "recovery": recovery,
                "budget": budget_snapshot(task),
                "checkpoints": incomplete_checkpoints(task),
            },
        )
    except Exception:
        pass
    return {
        "action": "blocked",
        "status": "blocked",
        "acceptance": acc,
        "event": evt,
        "blocker_code": blocker_code,
        "recovery": recovery,
    }


def close_task_after_stream(
    task,
    *,
    user_stopped: bool = False,
    interrupt_detected: bool = False,
    acceptance: Optional[Dict[str, Any]] = None,
    auto_continue_attempted: bool = False,
    auto_continue_succeeded: bool = False,
    tool_error: bool = False,
) -> Dict[str, Any]:
    """SSE / 对话流结束后强制收口。

    规则：
    - 用户停止 → cancelled
    - 审批中断 → waiting_approval（保持 awaiting_approval）
    - 验收通过且有证据 → completed（已由 apply_acceptance 处理则保留）
    - 未完成检查点且预算足够：若本轮已尝试续跑仍未完成 → blocked + 恢复方式
    - 预算耗尽 / 工具异常 → blocked
    - 禁止无原因的 running 悬挂
    """
    if task is None:
        return {"action": "no_task", "status": ""}

    from orchestrator_integration.agent_runtime import (
        append_event,
        apply_acceptance,
        finalize_cancel,
        request_cancel,
    )

    try:
        task.refresh_from_db()
    except Exception:
        pass

    st = str(getattr(task, "status", "") or "")
    if st in TERMINAL_STATUSES:
        return {
            "action": "already_terminal",
            "status": normalize_status(st),
            "acceptance": getattr(task, "acceptance", None) or acceptance,
        }

    if interrupt_detected or st == "awaiting_approval":
        if st != "awaiting_approval":
            from orchestrator_integration.models import AgentTask

            AgentTask.objects.filter(pk=task.pk).exclude(
                status__in=list(TERMINAL_STATUSES) + ["cancelling"]
            ).update(
                status="awaiting_approval",
                updated_at=timezone.now(),
                error_message="waiting_approval: 等待用户确认后恢复",
            )
            task.status = "awaiting_approval"
            try:
                append_event(
                    task,
                    "stream_closure",
                    status="awaiting_approval",
                    payload={
                        "blocker_code": "waiting_approval",
                        "recovery": "在对话中批准工具写入/审批后继续，或调用 resume API。",
                    },
                )
            except Exception:
                pass
        return {
            "action": "waiting_approval",
            "status": "waiting_approval",
            "recovery": "批准或拒绝后 resume；勿重新下发整单。",
        }

    if user_stopped or st == "cancelling":
        if st == "cancelling":
            fin = finalize_cancel(task, force=True)
        else:
            request_cancel(task)
            try:
                task.refresh_from_db()
            except Exception:
                pass
            fin = finalize_cancel(task, force=True)
        return {
            "action": "cancelled",
            "status": "cancelled",
            "finalize": fin,
            "recovery": "任务已取消；需要结果请新开任务或明确续跑指令。",
        }

    # 验收已明确通过 → completed
    if acceptance and acceptance.get("ok"):
        # deferred_completion 可能仍把库留在 running
        if st in OPEN_STATUSES or st == "running":
            # 再验检查点：无完成证据不得 completed
            chk = incomplete_checkpoints(task)
            if chk["incomplete"]:
                return _block_task(
                    task,
                    blocker_code="acceptance_without_checkpoints",
                    reasons=chk["reasons"]
                    or ["验收标记通过但业务检查点未完成"],
                    recovery="发送「继续」保留原任务与授权，完成剩余检查点后再交付。",
                    acceptance=acceptance,
                )
            # 阶段4：证据门禁（缺失/矛盾 → blocked）
            try:
                from orchestrator_integration.result_evidence_delivery import (
                    gate_completion_on_evidence,
                )

                gated = gate_completion_on_evidence(task, acceptance)
                acceptance = gated.get("acceptance") or acceptance
                if not acceptance.get("ok"):
                    return _block_task(
                        task,
                        blocker_code="evidence_gate_failed",
                        reasons=list(acceptance.get("reasons") or ["证据门禁未通过"]),
                        recovery=str(
                            acceptance.get("recovery")
                            or "补齐可追溯证据或消除矛盾后发送「继续」。"
                        ),
                        acceptance=acceptance,
                    )
            except Exception:
                pass
            evt = apply_acceptance(task, acceptance)
            try:
                append_event(
                    task,
                    "stream_closure",
                    status="completed",
                    payload={"acceptance_ok": True},
                )
            except Exception:
                pass
            return {
                "action": "completed",
                "status": "completed",
                "event": evt,
                "acceptance": acceptance,
            }
        return {
            "action": "keep",
            "status": normalize_status(st),
            "acceptance": acceptance,
        }

    # 验收已失败且已 blocked
    if acceptance is not None and not acceptance.get("ok"):
        if st in OPEN_STATUSES or st == "running":
            return _block_task(
                task,
                blocker_code="acceptance_failed",
                reasons=list(acceptance.get("reasons") or ["业务验收未通过"]),
                recovery=str(
                    acceptance.get("recovery")
                    or "查看 acceptance.reasons 与工具明细；修复后发送「继续」。"
                ),
                acceptance=acceptance,
            )
        return {
            "action": "keep",
            "status": normalize_status(st),
            "acceptance": acceptance,
        }

    chk = incomplete_checkpoints(task)
    has_budget = budget_remaining(task)

    if tool_error and chk["incomplete"]:
        return _block_task(
            task,
            blocker_code="tool_error_incomplete",
            reasons=chk["reasons"] + ["工具调用异常，任务未完成"],
            recovery="检查工具错误与环境后发送「继续」；未知结果先核对再重试。",
            acceptance=acceptance,
        )

    if chk["incomplete"] and not has_budget:
        snap = chk["budget"]
        return _block_task(
            task,
            blocker_code="budget_exhausted",
            reasons=chk["reasons"]
            + [
                f"工具预算已耗尽 "
                f"{snap['used_tool_actions']}/{snap['max_tool_actions']}"
            ],
            recovery="提高预算或缩小目标后发送「继续」；勿要求用户点名工具。",
            acceptance=acceptance,
        )

    if chk["incomplete"] and has_budget:
        # 本轮应已尝试 auto-continue；仍未完成则明确阻塞，禁止悬挂 running
        if auto_continue_attempted and auto_continue_succeeded:
            # 续跑成功但检查点仍未齐：再给 blocked（模型可能只说了话没做完）
            return _block_task(
                task,
                blocker_code="incomplete_after_continue",
                reasons=chk["reasons"],
                recovery="发送「继续」完成剩余检查点；系统将保留原任务进度与授权。",
                acceptance=acceptance,
            )
        if auto_continue_attempted and not auto_continue_succeeded:
            return _block_task(
                task,
                blocker_code="auto_continue_failed",
                reasons=chk["reasons"] + ["服务端自动续跑未完成业务检查点"],
                recovery="发送「继续」或检查执行器/浏览器会话后恢复；勿重新下发整单。",
                acceptance=acceptance,
            )
        # 未尝试续跑（例如无 commission 路径）：仍不得留 running
        return _block_task(
            task,
            blocker_code="incomplete_checkpoints",
            reasons=chk["reasons"] + ["对话流已结束但业务检查点未完成"],
            recovery="发送「继续」自动续跑未完成步骤；预算充足时不应重新挂载工具。",
            acceptance=acceptance,
        )

    # 无明确检查点（expected=0）但仍 running：流结束必须收口
    if st in OPEN_STATUSES or st == "running":
        if acceptance and acceptance.get("ok"):
            evt = apply_acceptance(task, acceptance)
            return {"action": "completed", "status": "completed", "event": evt}
        return _block_task(
            task,
            blocker_code="stream_ended_without_terminal",
            reasons=["对话流结束时任务仍为执行中，且无完整验收证据"],
            recovery="若任务应继续，发送「继续」；若已完成请补充证据后 assert/交付。",
            acceptance=acceptance
            or {"ok": False, "reasons": ["stream_ended_without_terminal"]},
        )

    return {
        "action": "keep",
        "status": normalize_status(st),
        "acceptance": acceptance,
    }


def reconcile_stale_running_tasks(
    *,
    older_than_seconds: int = 900,
    limit: int = 50,
) -> Dict[str, Any]:
    """进程重启后：租约过期仍 running 的任务 → blocked。"""
    from datetime import timedelta

    from orchestrator_integration.models import AgentTask

    cutoff = timezone.now() - timedelta(seconds=max(60, older_than_seconds))
    qs = (
        AgentTask.objects.filter(status="running", updated_at__lt=cutoff)
        .order_by("updated_at")[:limit]
    )
    closed = []
    for task in qs:
        lease_exp = getattr(task, "lease_expires_at", None)
        if lease_exp and lease_exp > timezone.now():
            continue
        result = _block_task(
            task,
            blocker_code="process_interrupted",
            reasons=["执行进程中断或租约过期，任务未正常收口"],
            recovery="发送「继续」从已保存进度恢复；有副作用步骤先核对再执行。",
        )
        closed.append(
            {
                "task_id": task.public_task_id or str(task.pk),
                "result": result.get("action"),
            }
        )
    return {"closed": closed, "count": len(closed)}
