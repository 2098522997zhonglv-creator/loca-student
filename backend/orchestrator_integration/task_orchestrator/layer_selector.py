"""三层工具选择：一层当前执行 / 二层自动备用 / 三层待授权。

分层对象是任务中的具体操作及对象，不是整个 Skill 的永久等级。
LLM 结论是候选；权限仅来自已有授权与系统强制审批。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from orchestrator_integration.task_orchestrator.capability_catalog import (
    capability_exists,
    get_capability,
)
from orchestrator_integration.task_orchestrator.plan import (
    NODE_WAITING_USER,
    PlanNode,
    TaskPlan,
)

LAYER_CURRENT = 1
LAYER_FALLBACK = 2
LAYER_PENDING_AUTH = 3

# 明确禁止进入可申请清单的能力（系统级）
_HARD_FORBIDDEN_CAPS = frozenset(
    {
        "pay",
        "payment",
        "checkout_pay",
    }
)

# 动作 → 可能涉及的能力（用于授权映射）
_ACTION_CAP_HINTS: Dict[str, Set[str]] = {
    "add_to_cart": {"playwright-skill", "playwright-cli", "browser-use"},
    "upload": {"playwright-skill", "playwright-cli"},
    "submit_order": {"playwright-skill", "playwright-cli"},
    "execute_testcase": {"ui-automation"},
    "knowledge_search": {"knowledge_search"},
    "platform_write": {"loca-stude"},
}


@dataclass
class LayerDecision:
    layer1_mount: List[str] = field(default_factory=list)
    layer2_discoverable: List[str] = field(default_factory=list)
    layer3_pending: List[Dict[str, Any]] = field(default_factory=list)
    forbidden_excluded: List[str] = field(default_factory=list)
    reason: str = ""

    def mount_skills(self) -> Set[str]:
        """一层真实挂载的 Skill 名（不含纯 builtin）。"""
        builtins = {
            "knowledge_search",
            "read_skill_content",
            "execute_skill_script",
            "request_executor_heal",
            "assert_task_postcondition",
            "report_task_blocked",
            "request_tool_expansion",
        }
        out = set()
        for c in self.layer1_mount:
            if c in builtins or c.startswith("mcp:"):
                continue
            out.add(c)
        # execute 始终需要脚本通道
        return out

    def builtin_names(self) -> Set[str]:
        known = {
            "knowledge_search",
            "read_skill_content",
            "execute_skill_script",
            "request_executor_heal",
            "assert_task_postcondition",
            "report_task_blocked",
        }
        return {c for c in self.layer1_mount if c in known} | {
            "read_skill_content",
            "execute_skill_script",
            "request_executor_heal",
            "assert_task_postcondition",
            "report_task_blocked",
        }


def _authorized_caps(plan: TaskPlan) -> Set[str]:
    """已有授权范围内可自动使用的能力。"""
    scope = plan.authorization_scope or {}
    allow = set(scope.get("allow_actions") or [])
    deny = set(scope.get("deny_actions") or []) | set(plan.forbidden_actions or [])
    # 只读/查询类默认在授权内
    caps: Set[str] = {
        "knowledge_search",
        "read_skill_content",
        "execute_skill_script",
        "request_executor_heal",
        "assert_task_postcondition",
        "report_task_blocked",
        "url-reader",
        "loca-stude",
        "ui-automation",
        "api-automation",
        "playwright-skill",
        "playwright-cli",
    }
    # 浏览器写操作：仅当动作授权时保留对应 skill 的「执行权」语义；
    # skill 本身仍可挂载，具体 submit_order 由 pending_auth / action_auth 卡。
    if "pay" in deny or "pay" in _HARD_FORBIDDEN_CAPS:
        pass
    # 从 allow 扩展
    for action in allow:
        caps |= _ACTION_CAP_HINTS.get(action, set())
    # 用户禁止的动作对应能力不进入一层写路径（仍可在 L3）
    _ = deny
    return caps


def select_layers_for_node(
    plan: TaskPlan,
    node: Optional[PlanNode] = None,
    *,
    activated_fallbacks: Optional[Set[str]] = None,
) -> LayerDecision:
    """为当前节点生成三层决策。"""
    node = node or plan.current()
    if node is None:
        return LayerDecision(reason="无当前节点")

    auth_caps = _authorized_caps(plan)
    forbidden = set(plan.forbidden_actions or []) | set(
        (plan.authorization_scope or {}).get("deny_actions") or []
    )
    activated = set(activated_fallbacks or [])

    layer1: List[str] = []
    layer2: List[str] = []
    layer3: List[Dict[str, Any]] = []
    excluded: List[str] = []

    # 三层：节点显式待授权动作
    for action in node.pending_auth_actions or []:
        if action in _HARD_FORBIDDEN_CAPS or action in forbidden:
            excluded.append(action)
            continue
        # 已批准则升为一层
        approved = any(
            p.target == action and p.approval == "approved" and p.node_id == node.node_id
            for p in plan.pending_auth
        )
        denied = any(
            p.target == action and p.approval == "denied" and p.node_id == node.node_id
            for p in plan.pending_auth
        )
        if denied:
            excluded.append(action)
            continue
        if approved:
            for cap in _ACTION_CAP_HINTS.get(action, set()):
                if cap not in layer1:
                    layer1.append(cap)
            if "execute_skill_script" not in layer1:
                layer1.append("execute_skill_script")
            continue
        layer3.append(
            {
                "action_id": f"{node.node_id}:{action}",
                "node_id": node.node_id,
                "target": action,
                "impact_summary": f"将在节点「{node.goal}」执行 {action}",
                "layer": LAYER_PENDING_AUTH,
            }
        )

    for cap in node.primary_candidates or []:
        if cap in _HARD_FORBIDDEN_CAPS:
            excluded.append(cap)
            continue
        if not capability_exists(cap) and not cap.startswith("mcp:"):
            # 仍允许常见 skill 名
            pass
        if cap in auth_caps or cap in {
            "knowledge_search",
            "read_skill_content",
            "execute_skill_script",
            "playwright-skill",
            "ui-automation",
            "loca-stude",
        }:
            if cap not in layer1:
                layer1.append(cap)
        else:
            layer3.append(
                {
                    "action_id": f"{node.node_id}:cap:{cap}",
                    "node_id": node.node_id,
                    "target": cap,
                    "impact_summary": f"能力「{cap}」超出当前授权，需批准后使用",
                    "layer": LAYER_PENDING_AUTH,
                }
            )

    for cap in node.fallback_candidates or []:
        if cap in _HARD_FORBIDDEN_CAPS or cap in excluded:
            excluded.append(cap)
            continue
        if cap in layer1:
            continue
        if cap in auth_caps or cap in {
            "knowledge_search",
            "read_skill_content",
            "playwright-cli",
            "request_executor_heal",
            "url-reader",
            "loca-stude",
        }:
            if cap in activated:
                if cap not in layer1:
                    layer1.append(cap)
            elif cap not in layer2:
                layer2.append(cap)
        else:
            layer3.append(
                {
                    "action_id": f"{node.node_id}:fallback:{cap}",
                    "node_id": node.node_id,
                    "target": cap,
                    "impact_summary": f"备用能力「{cap}」需授权",
                    "layer": LAYER_PENDING_AUTH,
                }
            )

    # 有待授权且无一可执行一层能力 → 节点等待用户
    if layer3 and not layer1 and node.pending_auth_actions:
        node.status = NODE_WAITING_USER

    return LayerDecision(
        layer1_mount=layer1,
        layer2_discoverable=layer2,
        layer3_pending=layer3,
        forbidden_excluded=sorted(set(excluded)),
        reason=f"node={node.node_id}",
    )


def activate_fallback(
    plan: TaskPlan,
    capability_id: str,
    *,
    reason: str = "",
    activated_fallbacks: Optional[Set[str]] = None,
) -> tuple[bool, str, LayerDecision]:
    """二层自动启用：已有授权内，不向用户申请扩权。"""
    cap = (capability_id or "").strip()
    node = plan.current()
    if not node:
        return False, "无当前节点", LayerDecision()
    activated = set(activated_fallbacks or [])
    decision = select_layers_for_node(plan, node, activated_fallbacks=activated)
    if cap in decision.layer1_mount:
        return True, f"「{cap}」已在一层挂载", decision
    if cap not in decision.layer2_discoverable and cap not in (
        node.fallback_candidates or []
    ):
        # 扩大检索：若能力真实存在且在授权内，允许纳入二层（FR04 未命中可扩大）
        if capability_exists(cap) or get_capability(cap):
            auth = _authorized_caps(plan)
            if cap not in auth and cap not in {
                "playwright-skill",
                "playwright-cli",
                "ui-automation",
                "loca-stude",
                "knowledge_search",
            }:
                return (
                    False,
                    f"「{cap}」不在已有授权/二层候选，不能静默扩权",
                    decision,
                )
        else:
            return False, f"能力「{cap}」不存在", decision

    activated.add(cap)
    new_decision = select_layers_for_node(
        plan, node, activated_fallbacks=activated
    )
    if cap not in new_decision.layer1_mount:
        # 强制并入一层（已授权校验已过）
        new_decision.layer1_mount = list(new_decision.layer1_mount) + [cap]
        new_decision.layer2_discoverable = [
            c for c in new_decision.layer2_discoverable if c != cap
        ]
    msg = f"已自动启用二层备用「{cap}」"
    if reason:
        msg += f"（{reason[:120]}）"
    return True, msg, new_decision


def describe_pending_for_user(pending: List[Dict[str, Any]]) -> str:
    if not pending:
        return ""
    lines = ["需要你的批准后才能继续："]
    for p in pending:
        lines.append(
            f"- 动作: {p.get('target')}｜节点: {p.get('node_id')}｜"
            f"影响: {p.get('impact_summary')}"
        )
    lines.append("批准后将在同一任务原节点续跑，不重跑已成功步骤。")
    return "\n".join(lines)
