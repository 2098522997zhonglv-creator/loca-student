"""编排硬门禁：input_refs、跨工具对象 ID、弱断言拒绝、L3 批准同步。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set

from orchestrator_integration.task_orchestrator.execution_refs import (
    call_success_is_not_test_pass,
)
from orchestrator_integration.task_orchestrator.node_runtime import (
    extract_execution_ids,
)
from orchestrator_integration.task_orchestrator.plan import (
    PlanNode,
    TaskPlan,
    plan_from_dict,
)
from orchestrator_integration.task_orchestrator.runtime_bridge import (
    apply_orchestrator_mount,
    mark_pending_approval,
)

# 交付物类 refs：主流程跑之前不强制具备（跑完才产出）
_DELIVERY_SOFT_REFS = frozenset(
    {
        "order_sn",
        "cart_id",
        "cart_item_id",
        "screenshots",
        "page_facts",
        "assertions",
        "summary",
        "parametrize_results",
        "cleanup_status",
        "cleanup_errors",
    }
)

# prepare 产出：有 prepare_data 节点时，进入 run 至少要有其一
_PREPARE_GROUP = frozenset(
    {"prepared_object_ids", "object_id", "extracted_vars"}
)

_WEAK_URL_ORDER = re.compile(
    r"(?i)(url|链接|地址|href).{0,48}order|"
    r"order.{0,24}(url|链接|地址)|"
    r"(通过|成功|证明|断言).{0,24}(url|链接).{0,24}order|"
    r"(url|链接).{0,24}order.{0,24}(通过|成功|证明)"
)
_CLAIM_PASS = re.compile(
    r"(?i)(测试通过|用例通过|验收通过|断言通过|任务完成|全部通过|已通过)"
)
_HAS_REAL_PASS = re.compile(
    r'(?i)test_result["\s:=]+passed|"status"\s*:\s*"passed"|'
    r"断言结果\s*[:=]\s*pass|business_passed\s*[:=]\s*true"
)
_OUTER_SUCCESS = re.compile(
    r'(?i)"status"\s*:\s*"success"|succeeded|工具调用成功|外层\s*success'
)


def _fact_value(facts: Dict[str, Any], key: str) -> Any:
    if not isinstance(facts, dict):
        return None
    if key in facts and facts[key] not in (None, "", [], {}):
        v = facts[key]
        if isinstance(v, dict) and "value" in v:
            return v.get("value")
        return v
    # nested execution_ref / case_ref
    ref = facts.get("execution_ref") if isinstance(facts.get("execution_ref"), dict) else {}
    if key in ref and ref[key] not in (None, ""):
        return ref[key]
    case_ref = facts.get("case_ref") if isinstance(facts.get("case_ref"), dict) else {}
    if key in ("case_ref", "case_id", "testcase_id"):
        if key == "case_ref" and case_ref:
            return case_ref
        if case_ref.get("case_id") not in (None, ""):
            return case_ref.get("case_id")
    return None


def _has_any(facts: Dict[str, Any], keys: Set[str]) -> bool:
    return any(_fact_value(facts, k) not in (None, "", [], {}) for k in keys)


def check_input_refs(
    plan: Optional[TaskPlan],
    node: Optional[PlanNode],
    facts: Optional[Dict[str, Any]],
) -> Optional[str]:
    """缺必备 input_refs 时返回错误说明（硬拦）。"""
    if plan is None or node is None:
        return None
    facts = facts if isinstance(facts, dict) else {}
    refs = [r for r in (node.input_refs or []) if r and r not in _DELIVERY_SOFT_REFS]
    if not refs:
        # 即便未声明，复杂计划的 run 节点仍要求 prepare 产出
        node_ids = {n.node_id for n in plan.nodes}
        if node.node_id == "run_or_browser" and "prepare_data" in node_ids:
            prep_done = any(
                n.node_id == "prepare_data" and n.status == "done" for n in plan.nodes
            )
            if prep_done and not _has_any(facts, _PREPARE_GROUP):
                return (
                    "input_refs 硬门禁：prepare_data 已完成但 facts 无 "
                    "prepared_object_ids/object_id/extracted_vars；"
                    "禁止进入 run_or_browser。"
                )
        return None

    missing: List[str] = []
    prepare_refs = [r for r in refs if r in _PREPARE_GROUP]
    other_refs = [r for r in refs if r not in _PREPARE_GROUP]

    if prepare_refs:
        node_ids = {n.node_id for n in plan.nodes}
        if "prepare_data" in node_ids or "api_prepare_or_run" in node_ids:
            if not _has_any(facts, set(prepare_refs) | _PREPARE_GROUP):
                missing.append(
                    "/".join(prepare_refs) + "(prepare 产出缺一不可)"
                )

    for r in other_refs:
        if r in {"case_ref", "testcase_id", "case_id"}:
            if not (
                _fact_value(facts, "case_ref")
                or _fact_value(facts, "testcase_id")
                or _fact_value(facts, "case_id")
            ):
                missing.append(r)
            continue
        if r in {"execution_id", "record_id"}:
            # run 之后才有；进入 assert 才强制
            if node.node_id in {"assert_and_deliver", "cleanup_data"}:
                if not (
                    _fact_value(facts, "execution_id")
                    or _fact_value(facts, "record_id")
                    or _fact_value(facts, "task_execution_id")
                ):
                    missing.append(r)
            continue
        if _fact_value(facts, r) in (None, "", [], {}):
            missing.append(r)

    if not missing:
        return None
    return (
        f"input_refs 硬门禁：节点「{node.node_id}」缺少 "
        + ", ".join(missing)
        + "。请先完成依赖节点并写入 facts，禁止跳步执行。"
    )


def assert_cross_tool_object_ids(
    facts: Optional[Dict[str, Any]],
    tool_text: str,
) -> Optional[str]:
    """API→浏览器等跨工具传递时，对象 ID 必须原样一致。"""
    facts = facts if isinstance(facts, dict) else {}
    ids = extract_execution_ids(tool_text or "")
    checks = (
        ("object_id", _fact_value(facts, "object_id")),
        ("order_sn", _fact_value(facts, "order_sn")),
        ("cart_id", _fact_value(facts, "cart_id")),
        ("execution_id", _fact_value(facts, "execution_id")),
        ("record_id", _fact_value(facts, "record_id")),
    )
    for key, expected in checks:
        if expected in (None, "", [], {}):
            continue
        got = ids.get(key)
        if got in (None, ""):
            continue
        if isinstance(expected, (list, tuple, set)):
            allowed = {str(x) for x in expected}
            if str(got) not in allowed:
                return (
                    f"跨工具对象 ID 不一致：facts.{key}={list(allowed)}，"
                    f"工具输出 {key}={got}。禁止模型改写后传递。"
                )
        elif str(got) != str(expected):
            return (
                f"跨工具对象 ID 不一致：facts.{key}={expected}，"
                f"工具输出 {key}={got}。禁止模型改写后传递。"
            )
    # prepared_object_ids 列表：工具产出的 object_id 必须落在集合内
    prepared = _fact_value(facts, "prepared_object_ids")
    if prepared and ids.get("object_id"):
        bag = prepared if isinstance(prepared, (list, tuple, set)) else [prepared]
        if str(ids["object_id"]) not in {str(x) for x in bag}:
            return (
                f"跨工具对象 ID 不一致：object_id={ids['object_id']} "
                f"不在 prepared_object_ids={list(bag)} 内。"
            )
    return None


def reject_weak_assertions(
    tool_results: Optional[Sequence[str]] = None,
    *,
    facts: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """拒绝弱断言假通过；返回原因列表（空=未发现弱断言过关声明）。"""
    joined = "\n".join(str(x) for x in (tool_results or []) if x)
    reasons: List[str] = []
    if not joined.strip():
        return reasons

    if _WEAK_URL_ORDER.search(joined) and _CLAIM_PASS.search(joined):
        reasons.append("弱断言拒绝：URL 含 order 不能证明下单/业务终态通过")

    if _CLAIM_PASS.search(joined) and not _HAS_REAL_PASS.search(joined):
        if _OUTER_SUCCESS.search(joined) or "succeeded" in joined.lower():
            reasons.append(
                "弱断言拒绝：外层 success/succeeded 不能当作测试通过；"
                "须有 test_result=passed 或真实断言结果"
            )

    # 与 execution_refs 口径对齐
    note = call_success_is_not_test_pass(
        tool_succeeded=bool(_OUTER_SUCCESS.search(joined)),
        test_result=(facts or {}).get("test_result")
        if isinstance(facts, dict)
        else None,
    )
    if note and _CLAIM_PASS.search(joined):
        if note not in reasons:
            reasons.append("弱断言拒绝：" + note)

    # 仅有 status=success 的 UI 结果却宣称通过
    if re.search(r'(?i)"status"\s*:\s*"success"', joined) and _CLAIM_PASS.search(
        joined
    ):
        if not re.search(r'(?i)test_result|passed_steps|断言', joined):
            reasons.append(
                "弱断言拒绝：UI status=success 仅表示执行结束态，不等于业务断言通过"
            )

    return reasons


def apply_l3_approval_to_task(
    task,
    *,
    approve: bool,
    targets: Optional[Sequence[str]] = None,
    action_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """将用户批准/拒绝同步到 facts.task_plan.pending_auth，并刷新挂载。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask

    facts = dict(getattr(task, "facts", None) or {})
    raw = facts.get("task_plan")
    if not isinstance(raw, dict):
        return {"ok": False, "error_code": "no_task_plan", "updated": []}

    plan = plan_from_dict(raw)
    approval = "approved" if approve else "denied"
    updated: List[str] = []
    want_targets = {str(t) for t in (targets or []) if t}
    want_ids = {str(a) for a in (action_ids or []) if a}

    for p in plan.pending_auth:
        hit = False
        if want_ids and (p.action_id in want_ids or p.target in want_ids):
            hit = True
        if want_targets and p.target in want_targets:
            hit = True
        # 未指定则批准当前节点全部 pending
        if not want_targets and not want_ids:
            if p.node_id == plan.current_node and p.approval == "pending":
                hit = True
        if hit and p.approval == "pending":
            mark_pending_approval(plan, p.action_id, approval)
            updated.append(p.action_id)

    if not updated and (want_targets or want_ids):
        # 按 target 再试一次 mark
        for key in list(want_targets) + list(want_ids):
            mark_pending_approval(plan, key, approval)
            updated.append(key)

    activated = set(facts.get("activated_fallbacks") or [])
    mount = apply_orchestrator_mount(plan, activated_fallbacks=activated)
    facts["task_plan"] = plan.to_dict()
    facts["activated_fallbacks"] = sorted(activated | set(mount.activated_fallbacks))
    auth = dict(getattr(task, "auth_scope", None) or {})
    auth.update(mount.to_auth_scope_patch())
    if approve:
        allow = set(auth.get("allow_actions") or [])
        for p in plan.pending_auth:
            if p.approval == "approved" and p.target:
                allow.add(p.target)
        auth["allow_actions"] = sorted(allow)

    AgentTask.objects.filter(pk=task.pk).update(
        facts=facts,
        auth_scope=auth,
        updated_at=timezone.now(),
    )
    task.facts = facts
    task.auth_scope = auth
    return {
        "ok": True,
        "approval": approval,
        "updated": updated,
        "remount_skills": sorted(mount.skill_allowlist),
        "current_node": plan.current_node,
        "plan_status": plan.status,
    }
