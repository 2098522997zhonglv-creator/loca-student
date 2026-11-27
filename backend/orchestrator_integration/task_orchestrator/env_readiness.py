"""第 3 阶段：执行环境就绪检查。

缺失能力时立即 blocked，列出缺口与恢复路径；不交给模型虚报已执行。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from django.utils import timezone


def probe_required_capabilities(
    *,
    required_skills: Optional[Sequence[str]] = None,
    required_builtins: Optional[Sequence[str]] = None,
    mount_execute: bool = True,
    executor_mounted: bool = True,
    skill_names_available: Optional[Sequence[str]] = None,
    mcp_ok: bool = True,
    browser_required: bool = False,
    browser_alive: Optional[bool] = None,
) -> Dict[str, Any]:
    """探测任务所需能力是否可用。"""
    missing: List[Dict[str, str]] = []
    recovery: List[str] = []

    available: Set[str] = set(skill_names_available or [])
    # 若未提供 available 列表，尝试从 DB 读取
    if skill_names_available is None:
        try:
            from skills.models import Skill

            available = set(
                Skill.objects.filter(is_active=True).values_list("name", flat=True)
            )
        except Exception:
            available = set()

    for name in required_skills or []:
        n = str(name or "").strip()
        if not n:
            continue
        if n not in available:
            missing.append(
                {
                    "capability": n,
                    "kind": "skill",
                    "reason": "skill_missing_or_inactive",
                }
            )
            recovery.append(f"在项目 Skills 中启用或安装「{n}」后发送「继续」。")

    if mount_execute and not executor_mounted:
        missing.append(
            {
                "capability": "execute_skill_script",
                "kind": "builtin",
                "reason": "executor_not_mounted",
            }
        )
        recovery.append(
            "服务端将自动挂载执行工具；勿要求用户重挂。若仍失败请检查分区 allowlist。"
        )

    for b in required_builtins or []:
        # builtins 由运行时挂载；此处仅记录期望
        _ = b

    if not mcp_ok:
        missing.append(
            {
                "capability": "mcp",
                "kind": "mcp",
                "reason": "mcp_unavailable",
            }
        )
        recovery.append("检查 MCP 连接与凭证后发送「继续」；可先用已挂载 Skill 路径。")

    if browser_required and browser_alive is False:
        missing.append(
            {
                "capability": "browser_session",
                "kind": "browser",
                "reason": "browser_not_alive",
            }
        )
        recovery.append(
            "重新建立 Playwright/浏览器会话后发送「继续」；勿重复下单或重放已成功副作用。"
        )

    ok = len(missing) == 0
    return {
        "ok": ok,
        "missing": missing,
        "recovery": recovery,
        "blocker_code": "missing_capability" if not ok else "",
        "message": (
            "执行环境缺少必要能力："
            + ", ".join(m["capability"] for m in missing)
            if not ok
            else "ready"
        ),
    }


def required_skills_from_manifest(manifest: Optional[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for step in (manifest or {}).get("steps") or []:
        for t in step.get("tools") or []:
            if t and t not in names:
                names.append(str(t))
    return names


def block_task_for_missing_env(
    task,
    probe: Dict[str, Any],
) -> Dict[str, Any]:
    """将任务标为 blocked，并持久化缺口与恢复路径。"""
    from orchestrator_integration.agent_runtime import append_event, apply_acceptance

    if task is None:
        return {"action": "no_task"}
    if probe.get("ok"):
        return {"action": "ready", "status": getattr(task, "status", "")}

    reasons = [probe.get("message") or "missing_capability"]
    reasons.extend(
        f"{m.get('capability')}:{m.get('reason')}" for m in (probe.get("missing") or [])
    )
    recovery = "；".join(probe.get("recovery") or []) or (
        "补齐缺失能力后发送「继续」，保留原任务进度与授权。"
    )
    acceptance = {
        "ok": False,
        "reasons": reasons,
        "blockers": [
            {
                "blocker_code": probe.get("blocker_code") or "missing_capability",
                "recovery": recovery,
                "missing": probe.get("missing") or [],
                "step": "env_readiness",
            }
        ],
        "recovery": recovery,
        "closure": True,
    }
    st = str(getattr(task, "status", "") or "")
    if st in {"completed", "failed", "cancelled", "cancelling"}:
        return {"action": "already_terminal", "status": st}

    evt = apply_acceptance(task, acceptance)
    try:
        facts = dict(getattr(task, "facts", None) or {})
        facts["env_readiness"] = {
            "ok": False,
            "missing": probe.get("missing") or [],
            "recovery": probe.get("recovery") or [],
            "checked_at": timezone.now().isoformat(),
        }
        task.facts = facts
        type(task).objects.filter(pk=task.pk).update(
            facts=facts, updated_at=timezone.now()
        )
        append_event(
            task,
            "env_readiness",
            status="blocked",
            payload=facts["env_readiness"],
        )
    except Exception:
        pass
    return {
        "action": "blocked",
        "status": "blocked",
        "acceptance": acceptance,
        "event": evt,
        "probe": probe,
    }
