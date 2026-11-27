"""第 3 阶段：工具与授权编排。

任务开始前明确：每步需要什么工具、工具风险等级、授权从哪里来；
「继续」不得静默扩大 allow_actions。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from orchestrator_integration.action_auth import (
    check_auth_actions,
    infer_actions_from_command,
    normalize_action,
)
from orchestrator_integration.execution_policy import command_side_effect


RiskLevel = str  # read | write | irreversible | l3 | execute | unknown


def classify_tool_risk(
    *,
    skill_name: str = "",
    command: str = "",
    tool_name: str = "",
) -> Dict[str, Any]:
    """统一工具风险分级。"""
    name = (skill_name or tool_name or "").strip()
    effect = command_side_effect(command or "", name or None)
    actions = {normalize_action(a) for a in infer_actions_from_command(command or "")}
    # 高风险业务动作 → L3
    l3_actions = {"pay", "submit_order", "checkout"}
    if actions & l3_actions or effect == "irreversible":
        level: RiskLevel = "l3" if (actions & l3_actions) else "irreversible"
    elif effect in {"write", "execute"}:
        level = "write" if effect == "write" else "execute"
    elif effect == "read":
        level = "read"
    else:
        # 内置只读工具
        if name in {
            "knowledge_search",
            "read_skill_content",
            "assert_task_postcondition",
            "report_task_blocked",
        }:
            level = "read"
        else:
            level = "unknown"
    return {
        "risk": level,
        "side_effect": effect,
        "actions": sorted(actions),
        "skill_name": name,
        "requires_write_confirmation": level in {"write", "execute", "unknown"},
        "requires_l3_approval": level in {"l3", "irreversible"},
        "callable_without_auth": level == "read",
    }


def build_step_tool_manifest(
    *,
    stage_plan: Optional[Sequence[Dict[str, Any]]] = None,
    task_plan: Optional[Dict[str, Any]] = None,
    auth_scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按任务生成步骤清单：工具、输入、产出、验收证据、授权来源。"""
    auth_scope = auth_scope or {}
    allow = [normalize_action(a) for a in (auth_scope.get("allow_actions") or [])]
    deny = [normalize_action(a) for a in (auth_scope.get("deny_actions") or [])]
    steps: List[Dict[str, Any]] = []

    nodes = []
    if isinstance(task_plan, dict) and task_plan.get("nodes"):
        nodes = list(task_plan.get("nodes") or [])
    elif stage_plan:
        for s in stage_plan:
            if not isinstance(s, dict):
                continue
            nodes.append(
                {
                    "node_id": s.get("stage_id") or s.get("node_id"),
                    "primary_candidates": s.get("tool_capabilities")
                    or s.get("skills")
                    or [],
                    "input_refs": s.get("input_refs") or [],
                    "output_keys": s.get("output_keys") or [],
                    "completion_checks": (
                        [s.get("acceptance")] if s.get("acceptance") else []
                    ),
                    "pending_auth_actions": s.get("auth_actions") or [],
                }
            )

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("node_id") or node.get("stage_id") or "")
        tools = list(node.get("primary_candidates") or []) + list(
            node.get("fallback_candidates") or []
        )
        # 规范化为字符串名
        tool_names: List[str] = []
        for t in tools:
            if isinstance(t, dict):
                tool_names.append(str(t.get("skill") or t.get("name") or ""))
            else:
                tool_names.append(str(t))
        tool_names = [x for x in tool_names if x]
        risks = [
            classify_tool_risk(skill_name=tn)
            for tn in tool_names
        ]
        pending = [
            normalize_action(a)
            for a in (node.get("pending_auth_actions") or [])
            if a
        ]
        steps.append(
            {
                "step_id": nid,
                "tools": tool_names,
                "inputs": list(node.get("input_refs") or []),
                "outputs": list(node.get("output_keys") or []),
                "acceptance_evidence": list(node.get("completion_checks") or []),
                "auth_source": {
                    "allow_actions": allow,
                    "deny_actions": deny,
                    "pending_auth_actions": pending,
                },
                "tool_risks": risks,
            }
        )

    return {
        "steps": steps,
        "auth_scope_snapshot": {
            "allow_actions": allow,
            "deny_actions": deny,
        },
        "hint": format_tool_auth_hint(steps, allow=allow, deny=deny),
    }


def format_tool_auth_hint(
    steps: Sequence[Dict[str, Any]],
    *,
    allow: Sequence[str],
    deny: Sequence[str],
) -> str:
    lines = ["【工具与授权编排】"]
    if allow:
        lines.append(f"- 已授权动作: {', '.join(allow)}")
    if deny:
        lines.append(f"- 禁止动作: {', '.join(deny)}")
    for s in steps[:12]:
        tools = ", ".join(s.get("tools") or []) or "(未指定)"
        acc = "; ".join(s.get("acceptance_evidence") or []) or "节点完成证据"
        lines.append(
            f"- 步骤 {s.get('step_id')}: 工具[{tools}]；"
            f"验收={acc}"
        )
    lines.append(
        "- 「继续」保持原授权边界；新增可写/高风险动作须走扩权批准，禁止要求用户重挂工具。"
    )
    return "\n".join(lines)


def merge_auth_scope_for_continue(
    prior: Optional[Dict[str, Any]],
    patch: Optional[Dict[str, Any]],
    *,
    require_l3_for_new_allows: bool = True,
) -> Dict[str, Any]:
    """续跑合并授权：deny 只增不减；新 allow 默认进入 pending，不静默扩权。"""
    prior = dict(prior or {})
    patch = dict(patch or {})
    allow: Set[str] = {
        normalize_action(a) for a in (prior.get("allow_actions") or []) if a
    }
    deny: Set[str] = {
        normalize_action(a) for a in (prior.get("deny_actions") or []) if a
    }
    deny |= {normalize_action(a) for a in (patch.get("deny_actions") or []) if a}

    patch_allow = {
        normalize_action(a) for a in (patch.get("allow_actions") or []) if a
    }
    new_allows = sorted(a for a in patch_allow if a not in allow and a not in deny)
    pending = list(prior.get("pending_auth_actions") or [])
    if require_l3_for_new_allows and new_allows:
        for a in new_allows:
            if a not in pending:
                pending.append(a)
        # 不把新 allow 并入生效集合
    else:
        allow |= patch_allow

    allow -= deny
    out = dict(prior)
    out["allow_actions"] = sorted(allow)
    out["deny_actions"] = sorted(deny)
    if pending:
        out["pending_auth_actions"] = pending
    # 其它键：已有不覆盖
    for k, v in patch.items():
        if k in {"allow_actions", "deny_actions", "pending_auth_actions"}:
            continue
        if k not in out:
            out[k] = v
    out["continue_auth_policy"] = {
        "require_l3_for_new_allows": require_l3_for_new_allows,
        "deferred_new_allows": new_allows,
    }
    return out


def authorize_tool_call(
    *,
    auth_scope: Optional[Dict[str, Any]],
    skill_name: str = "",
    command: str = "",
) -> Dict[str, Any]:
    """执行前统一授权判定。"""
    risk = classify_tool_risk(skill_name=skill_name, command=command)
    if risk.get("callable_without_auth"):
        return {"ok": True, "risk": risk, "reason": "read_allowed"}
    actions = set(risk.get("actions") or [])
    if not actions:
        # 无可识别业务动作：按 side_effect 保守处理
        if risk.get("risk") in {"write", "execute", "unknown", "l3", "irreversible"}:
            # 仍走 check_auth 空动作通常 ok；高风险无动作时要求 pending 或 allow 非空
            scope = auth_scope or {}
            if risk.get("requires_l3_approval") and not (
                scope.get("allow_actions") or scope.get("pending_auth_actions")
            ):
                return {
                    "ok": False,
                    "risk": risk,
                    "error_code": "l3_auth_required",
                    "message": "高风险动作缺少授权，请批准后再执行。",
                    "recovery": "在对话中批准扩权，或明确授权动作后发送「继续」。",
                }
        return {"ok": True, "risk": risk, "reason": "no_business_action"}
    verdict = check_auth_actions(
        auth_scope=auth_scope or {},
        claimed_actions=list(actions),
        skill_name=skill_name,
        command=command,
    )
    if not verdict.get("ok"):
        return {
            "ok": False,
            "risk": risk,
            "error_code": verdict.get("error_code") or "auth_denied",
            "message": verdict.get("message") or "动作未授权",
            "recovery": "使用已授权动作，或经 L3/写确认扩权后再继续；勿要求用户重挂工具。",
            "auth": verdict,
        }
    # pending 中的新动作尚未生效
    pending = {
        normalize_action(a)
        for a in ((auth_scope or {}).get("pending_auth_actions") or [])
        if a
    }
    if actions & pending:
        return {
            "ok": False,
            "risk": risk,
            "error_code": "auth_pending_approval",
            "message": f"动作 {sorted(actions & pending)} 待批准，尚未生效。",
            "recovery": "批准 pending_auth_actions 后再执行。",
        }
    return {"ok": True, "risk": risk, "auth": verdict}
