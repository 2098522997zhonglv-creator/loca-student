"""Runtime 桥接：计划 → 真实工具挂载 → 节点推进（FR04/FR05）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from orchestrator_integration.stage_plan import StageSpec
from orchestrator_integration.task_orchestrator.flag import STRATEGY_VERSION
from orchestrator_integration.task_orchestrator.layer_selector import (
    LAYER_FALLBACK,
    LayerDecision,
    activate_fallback,
    describe_pending_for_user,
    select_layers_for_node,
)
from orchestrator_integration.task_orchestrator.plan import (
    NODE_DONE,
    NODE_FAILED,
    NODE_PENDING,
    NODE_RUNNING,
    NODE_WAITING_USER,
    TASK_DONE,
    TASK_FAILED,
    TASK_RUNNING,
    TASK_WAITING_USER,
    TaskPlan,
)


@dataclass
class OrchestratorMount:
    """一次真实挂载结果；以运行中工具集合为准，不能只改提示词。"""

    plan: TaskPlan
    decision: LayerDecision
    skill_allowlist: Set[str]
    builtin_allow: Set[str]
    activated_fallbacks: Set[str] = field(default_factory=set)
    strategy_version: str = STRATEGY_VERSION
    skip_legacy_intersect: bool = True  # FR11：禁止旧规则二次裁剪

    def to_auth_scope_patch(self) -> Dict[str, Any]:
        return {
            "strategy_version": self.strategy_version,
            "allowed_skills": sorted(self.skill_allowlist),
            "layer1": list(self.decision.layer1_mount),
            "layer2": list(self.decision.layer2_discoverable),
            "layer3": list(self.decision.layer3_pending),
            "activated_fallbacks": sorted(self.activated_fallbacks),
            "current_node": self.plan.current_node,
            "estimated_nodes": self.plan.estimated_nodes,
        }


def apply_orchestrator_mount(
    plan: TaskPlan,
    *,
    activated_fallbacks: Optional[Set[str]] = None,
) -> OrchestratorMount:
    activated = set(activated_fallbacks or [])
    node = plan.current()
    if node and node.status == NODE_PENDING:
        node.status = NODE_RUNNING
    decision = select_layers_for_node(
        plan, node, activated_fallbacks=activated
    )
    skills = decision.mount_skills()
    # 保证脚本通道对应的常用 skill 不被空挂载
    if not skills and any(
        c in decision.layer1_mount
        for c in ("execute_skill_script", "read_skill_content")
    ):
        # 仍允许空 skill（纯 builtin 节点）
        pass
    builtins = decision.builtin_names()
    return OrchestratorMount(
        plan=plan,
        decision=decision,
        skill_allowlist=set(skills),
        builtin_allow=set(builtins),
        activated_fallbacks=activated,
    )


def advance_plan_node(
    plan: TaskPlan,
    *,
    accepted: bool,
    failed: bool = False,
    waiting_user: bool = False,
) -> TaskPlan:
    """节点完成后选取依赖已满足的下一节点（不结束整个任务流）。"""
    cur = plan.current()
    if cur is None:
        plan.status = TASK_DONE
        return plan
    if waiting_user or (
        cur.pending_auth_actions
        and any(
            p.node_id == cur.node_id and p.approval == "pending"
            for p in plan.pending_auth
        )
        and not accepted
    ):
        cur.status = NODE_WAITING_USER
        plan.status = TASK_WAITING_USER
        return plan
    if failed:
        cur.status = NODE_FAILED
        plan.status = TASK_FAILED
        return plan
    if not accepted:
        return plan

    cur.status = NODE_DONE
    done = {n.node_id for n in plan.nodes if n.status == NODE_DONE}
    # 按计划顺序找下一个依赖满足的节点
    ids = [n.node_id for n in plan.nodes]
    idx = ids.index(cur.node_id) if cur.node_id in ids else -1
    nxt = None
    for n in plan.nodes[idx + 1 :] if idx >= 0 else plan.nodes:
        if n.status in {NODE_DONE, NODE_FAILED}:
            continue
        if all(d in done for d in (n.depends_on or [])):
            nxt = n
            break
    if nxt is None:
        # 任意就绪
        for n in plan.nodes:
            if n.status in {NODE_DONE, NODE_FAILED}:
                continue
            if all(d in done for d in (n.depends_on or [])):
                nxt = n
                break
    if nxt is None:
        plan.status = TASK_DONE
        plan.current_node = cur.node_id
        return plan
    nxt.status = NODE_RUNNING
    plan.current_node = nxt.node_id
    plan.status = TASK_RUNNING
    return plan


def sync_plan_to_stage_specs(plan: TaskPlan) -> List[StageSpec]:
    """映射到现有 stage_plan 字段，便于 AgentTask.stage_plan 复用。"""
    specs: List[StageSpec] = []
    for n in plan.nodes:
        caps = list(n.primary_candidates or [])
        # 二层摘要进入能力列表但不强制 restrict 掉一层
        for c in n.fallback_candidates or []:
            if c not in caps:
                caps.append(c)
        specs.append(
            StageSpec(
                stage_id=n.node_id,
                goal=n.goal,
                tool_capabilities=caps,
                depends_on=list(n.depends_on or []),
                acceptance="; ".join(n.completion_checks or []) or n.goal,
                auth_actions=list(n.pending_auth_actions or []),
            )
        )
    return specs


def build_orchestrator_hint(mount: OrchestratorMount) -> str:
    plan = mount.plan
    node = plan.current()
    lines = [
        "# 任务编排（task-orchestrator）",
        f"- 策略版本: {plan.strategy_version}",
        f"- 任务目标: {plan.goal[:200]}",
        f"- 预计业务节点: {plan.estimated_nodes}（与工具调用次数分开）",
        f"- 当前节点: {plan.current_node}｜任务状态: {plan.status}",
    ]
    if node:
        lines.append(f"- 节点目标: {node.goal}")
        lines.append(
            f"- 一层挂载: {', '.join(mount.decision.layer1_mount) or '(无)'}"
        )
        lines.append(
            f"- 二层备用(已授权，缺条件/失败时自动启用，勿申请扩权): "
            + (", ".join(mount.decision.layer2_discoverable) or "(无)")
        )
    if mount.decision.layer3_pending:
        lines.append(describe_pending_for_user(mount.decision.layer3_pending))
    if plan.forbidden_actions:
        lines.append(
            "- 禁止动作: " + ", ".join(plan.forbidden_actions) + "（不可申请）"
        )
    lines.extend(
        [
            "- 节点完成后继续下一依赖已满足节点；知识库节点结束 ≠ 任务结束。",
            "- 禁止要求用户重发任务或点名工具；缺信息优先自取。",
            "- 工具 succeeded ≠ 业务完成；须满足节点完成条件与交付字段。",
            "- 旧分区/主辅扩权已由本编排替代；request_tool_expansion 映射为二层自动启用。",
        ]
    )
    return "\n".join(lines)


def try_activate_fallback_from_expansion(
    mount: OrchestratorMount,
    skill_name: str,
    reason: str = "",
) -> tuple[bool, str, OrchestratorMount]:
    """兼容旧 request_tool_expansion → 二层自动启用（同一选择器）。"""
    ok, detail, decision = activate_fallback(
        mount.plan,
        skill_name,
        reason=reason or "legacy_expansion",
        activated_fallbacks=mount.activated_fallbacks,
    )
    if not ok:
        return False, detail, mount
    activated = set(mount.activated_fallbacks) | {skill_name}
    new_mount = apply_orchestrator_mount(
        mount.plan, activated_fallbacks=activated
    )
    # 用最新 decision
    new_mount.decision = decision
    new_mount.skill_allowlist = decision.mount_skills() | set(
        new_mount.skill_allowlist
    )
    if skill_name and not skill_name.startswith("mcp:"):
        builtins = {
            "knowledge_search",
            "read_skill_content",
            "execute_skill_script",
            "request_executor_heal",
            "assert_task_postcondition",
            "report_task_blocked",
        }
        if skill_name not in builtins:
            new_mount.skill_allowlist.add(skill_name)
    return True, detail, new_mount


def mark_pending_approval(
    plan: TaskPlan, action_id: str, approval: str
) -> TaskPlan:
    for p in plan.pending_auth:
        if p.action_id == action_id or p.target == action_id:
            p.approval = approval
    if approval == "approved" and plan.status == TASK_WAITING_USER:
        plan.status = "running"
        node = plan.current()
        if node:
            node.status = NODE_RUNNING
    return plan
