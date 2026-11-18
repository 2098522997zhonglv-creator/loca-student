"""任务编排 Skill 与三层工具选择（需求 20261118）。

Skill 负责识别目标、选能力、规划路径；Runtime 负责校验、挂载、状态与连续调度。
LLM 分层结论是候选，不产生权限。功能开关关闭时回退旧 zone/commission 路径。
"""

from __future__ import annotations

from orchestrator_integration.task_orchestrator.budget_presets import (
    LayerBudgetPreset,
    build_layer_budget_preset,
    count_testcase_refs,
    infer_plan_kind,
)
from orchestrator_integration.task_orchestrator.execution_refs import (
    assert_same_execution,
    bind_execution_ref,
    make_case_ref,
)
from orchestrator_integration.task_orchestrator.capability_catalog import (
    CapabilitySummary,
    get_capability,
    list_capability_summaries,
    load_capability_catalog,
)
from orchestrator_integration.task_orchestrator.flag import (
    STRATEGY_VERSION,
    is_task_orchestrator_enabled,
)
from orchestrator_integration.task_orchestrator.layer_selector import (
    LAYER_CURRENT,
    LAYER_FALLBACK,
    LAYER_PENDING_AUTH,
    LayerDecision,
    activate_fallback,
    select_layers_for_node,
)
from orchestrator_integration.task_orchestrator.plan import (
    PendingAuthAction,
    PlanFact,
    PlanNode,
    TaskPlan,
    build_task_plan,
    plan_from_dict,
    revise_affected_nodes,
)
from orchestrator_integration.task_orchestrator.runtime_bridge import (
    OrchestratorMount,
    advance_plan_node,
    apply_orchestrator_mount,
    build_orchestrator_hint,
    mark_pending_approval,
    sync_plan_to_stage_specs,
)
from orchestrator_integration.task_orchestrator.node_runtime import (
    advance_orchestrator_on_task,
    apply_remount_to_context,
    extract_execution_ids,
    merge_ids_into_facts,
    orch_node_acceptance,
)
from orchestrator_integration.task_orchestrator.gates import (
    apply_l3_approval_to_task,
    assert_cross_tool_object_ids,
    check_input_refs,
    reject_weak_assertions,
)

__all__ = [
    "STRATEGY_VERSION",
    "is_task_orchestrator_enabled",
    "LayerBudgetPreset",
    "build_layer_budget_preset",
    "count_testcase_refs",
    "infer_plan_kind",
    "assert_same_execution",
    "bind_execution_ref",
    "make_case_ref",
    "CapabilitySummary",
    "get_capability",
    "list_capability_summaries",
    "load_capability_catalog",
    "LAYER_CURRENT",
    "LAYER_FALLBACK",
    "LAYER_PENDING_AUTH",
    "LayerDecision",
    "activate_fallback",
    "select_layers_for_node",
    "PendingAuthAction",
    "PlanFact",
    "PlanNode",
    "TaskPlan",
    "build_task_plan",
    "plan_from_dict",
    "revise_affected_nodes",
    "OrchestratorMount",
    "advance_plan_node",
    "apply_orchestrator_mount",
    "build_orchestrator_hint",
    "sync_plan_to_stage_specs",
    "advance_orchestrator_on_task",
    "apply_remount_to_context",
    "extract_execution_ids",
    "merge_ids_into_facts",
    "orch_node_acceptance",
    "mark_pending_approval",
    "apply_l3_approval_to_task",
    "assert_cross_tool_object_ids",
    "check_input_refs",
    "reject_weak_assertions",
]
