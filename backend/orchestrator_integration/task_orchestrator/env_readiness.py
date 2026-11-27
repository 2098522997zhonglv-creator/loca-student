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
    optional_skills: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """探测任务所需能力是否可用。

    required_skills：硬依赖（缺则 blocked）
    optional_skills：仅记录缺口，不阻塞启动
    """
    missing: List[Dict[str, str]] = []
    optional_missing: List[Dict[str, str]] = []
    recovery: List[str] = []

    available: Set[str] = set(skill_names_available or [])
    # 若未提供 available 列表，尝试从 DB + 打包目录解析
    if skill_names_available is None:
        available = resolve_available_skill_names()

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

    for name in optional_skills or []:
        n = str(name or "").strip()
        if not n or n in available:
            continue
        optional_missing.append(
            {
                "capability": n,
                "kind": "skill",
                "reason": "optional_skill_unavailable",
            }
        )

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
        "optional_missing": optional_missing,
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
    """兼容旧接口：返回清单中全部工具名（含 fallback）。"""
    names: List[str] = []
    for step in (manifest or {}).get("steps") or []:
        for t in step.get("tools") or []:
            if t and t not in names:
                names.append(str(t))
    return names


def required_primary_skills_for_probe(
    manifest: Optional[Dict[str, Any]],
    *,
    current_stage: str = "",
    completed_stage_ids: Optional[Sequence[str]] = None,
    task_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    """环境探测只要求「当前未完成步骤的主候选」，fallback 不硬拦。

    避免整份计划把 playwright-cli / url-reader 等备用路径当成启动前置，
    导致任务一开局就 blocked（真实回归里工具本可挂载运行）。
    """
    completed = {str(x) for x in (completed_stage_ids or []) if x}
    primary: List[str] = []
    optional: List[str] = []

    # 优先从 task_plan 节点读取 primary vs fallback
    nodes = []
    if isinstance(task_plan, dict):
        nodes = list(task_plan.get("nodes") or [])
    if not nodes:
        for step in (manifest or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            sid = str(step.get("step_id") or "")
            if sid and sid in completed:
                continue
            if current_stage and sid and sid != current_stage:
                # 只盯当前阶段；若无 current 则取第一个未完成
                if primary or optional:
                    continue
            tools = [str(t) for t in (step.get("tools") or []) if t]
            # manifest 未区分时：第一个视作 primary，其余 optional
            if tools:
                if tools[0] not in primary:
                    primary.append(tools[0])
                for t in tools[1:]:
                    if t not in optional and t not in primary:
                        optional.append(t)
            if current_stage and sid == current_stage:
                break
            if not current_stage and (primary or optional):
                break
        return {"primary": primary, "optional": optional}

    # 有结构化 plan：按节点
    selected = None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("node_id") or node.get("stage_id") or "")
        if nid and nid in completed:
            continue
        if current_stage and nid == current_stage:
            selected = node
            break
        if selected is None:
            selected = node
    if selected is None and nodes:
        selected = nodes[0] if isinstance(nodes[0], dict) else None

    def _names(raw) -> List[str]:
        out: List[str] = []
        for t in raw or []:
            if isinstance(t, dict):
                n = str(t.get("skill") or t.get("name") or "")
            else:
                n = str(t or "")
            if n and n not in out:
                out.append(n)
        return out

    if selected:
        primary = _names(selected.get("primary_candidates") or [])
        optional = _names(selected.get("fallback_candidates") or [])
        # 去掉与 primary 重复的 optional
        optional = [n for n in optional if n not in primary]

    return {"primary": primary, "optional": optional}


def resolve_available_skill_names(
    *,
    auth_scope: Optional[Dict[str, Any]] = None,
    mounted_tool_names: Optional[Sequence[str]] = None,
) -> Set[str]:
    """综合 DB / 挂载白名单 / 已挂载工具，判定可用 Skill。"""
    available: Set[str] = set()
    try:
        from skills.models import Skill

        available |= set(
            Skill.objects.filter(is_active=True).values_list("name", flat=True)
        )
    except Exception:
        pass

    scope = auth_scope or {}
    for key in ("allowed_skills", "layer1", "skill_allowlist"):
        for n in scope.get(key) or []:
            if n:
                available.add(str(n))

    for n in mounted_tool_names or []:
        if n:
            available.add(str(n))

    # 目录存在也算「可安装/可挂载」弱可用，避免误杀已打包 Skill
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parents[3] / "bundled_skills"
        if root.is_dir():
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                # 目录名或 SKILL.md name
                available.add(child.name)
                skill_md = child / "SKILL.md"
                if skill_md.is_file():
                    try:
                        text = skill_md.read_text(encoding="utf-8", errors="ignore")
                        for line in text.splitlines()[:20]:
                            if line.lower().startswith("name:"):
                                available.add(line.split(":", 1)[1].strip())
                                break
                    except Exception:
                        pass
    except Exception:
        pass

    return available


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
