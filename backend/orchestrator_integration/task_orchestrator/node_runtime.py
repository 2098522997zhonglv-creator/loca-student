"""编排节点运行时：验收启发、输出提取、阶段推进与同轮 Skill 扩权。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from orchestrator_integration.task_orchestrator.execution_refs import (
    bind_execution_ref,
    call_success_is_not_test_pass,
    make_case_ref,
    update_execution_status,
)
from orchestrator_integration.task_orchestrator.flag import STRATEGY_VERSION
from orchestrator_integration.task_orchestrator.plan import (
    NODE_DONE,
    NODE_FAILED,
    NODE_RUNNING,
    TASK_FAILED,
    TASK_RUNNING,
    TaskPlan,
    plan_from_dict,
)
from orchestrator_integration.task_orchestrator.runtime_bridge import (
    advance_plan_node,
    apply_orchestrator_mount,
)


_ID_PATTERNS = (
    ("record_id", re.compile(r'"record_id"\s*:\s*(\d+)|record_id\s*[:=]\s*(\d+)', re.I)),
    (
        "execution_id",
        re.compile(
            r'"execution_id"\s*:\s*"([^"]+)"|execution_id\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
    (
        "task_execution_id",
        re.compile(
            r'"task_execution_id"\s*:\s*"?([^",}\s]+)"?|task_execution_id\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
    (
        "testcase_id",
        re.compile(r'"testcase_id"\s*:\s*(\d+)|testcase_id\s*[:=]\s*(\d+)', re.I),
    ),
    ("case_id", re.compile(r'"case_id"\s*:\s*(\d+)|case_id\s*[:=]\s*(\d+)', re.I)),
    (
        "object_id",
        re.compile(
            r'"object_id"\s*:\s*"?([^",}\s]+)"?|object_id\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
)


def extract_structured_blob(text: str) -> Dict[str, Any]:
    """从工具输出中尽量解析 JSON 对象（取最后一个可解析块）。"""
    if not text:
        return {}
    # 整段即 JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # 扫描 {...}
    blobs: List[Dict[str, Any]] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = text[start : i + 1]
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        blobs.append(obj)
                except Exception:
                    pass
                start = -1
    return blobs[-1] if blobs else {}


def extract_execution_ids(text: str) -> Dict[str, Any]:
    blob = extract_structured_blob(text)
    out: Dict[str, Any] = {}
    for key in (
        "record_id",
        "execution_id",
        "task_execution_id",
        "testcase_id",
        "case_id",
        "object_id",
        "prepared_object_ids",
        "parametrize_results",
        "test_result",
        "status",
        "cleanup_status",
        "cleanup_errors",
    ):
        if key in blob and blob[key] not in (None, ""):
            out[key] = blob[key]
    for key, pat in _ID_PATTERNS:
        if key not in out:
            m = pat.search(text or "")
            if m:
                val = next((g for g in m.groups() if g is not None), None)
                if val is not None:
                    out[key] = int(val) if key.endswith("_id") and str(val).isdigit() else val
    return out


def orch_node_acceptance(
    stage_id: str, tool_results: Optional[List[str]] = None
) -> Dict[str, Any]:
    """编排节点专用验收（可推进则 ok）。"""
    joined = "\n".join(str(x) for x in (tool_results or []) if x)
    sid = stage_id or ""
    ids = extract_execution_ids(joined)

    if sid in {"query_cases"}:
        if any(
            k in joined
            for k in (
                "testcase_id",
                "case_id",
                "case_ref",
                "get_testcases",
                "get_testcase",
                "source_type",
                "ui_testcase",
                "api_scenario",
            )
        ) or ids.get("testcase_id") or ids.get("case_id"):
            return {"ok": True, "reason": "case_ref_found", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "missing_case_ref", "stage_id": sid}

    if sid in {"prepare_data", "api_prepare_or_run"}:
        if any(
            k in joined
            for k in (
                "prepared_object_ids",
                "object_id",
                "extracted_vars",
                "front_sql",
                "parametrize",
                "task_execution_id",
                "execution_id",
                "造数",
                "准备成功",
            )
        ) or ids.get("object_id") or ids.get("task_execution_id") or ids.get(
            "execution_id"
        ):
            return {"ok": True, "reason": "prepare_evidence", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "prepare_incomplete", "stage_id": sid}

    if sid in {"run_or_browser", "execute", "execute_testcase"}:
        if ids.get("record_id") or ids.get("execution_id") or any(
            k in joined
            for k in (
                "record_id",
                "execution_id",
                "parametrize_results",
                "终态",
                "execute_testcase",
                "u_case_result",
            )
        ):
            return {
                "ok": True,
                "reason": "run_execution_bound",
                "stage_id": sid,
                "ids": ids,
            }
        return {"ok": False, "reason": "missing_execution_ref", "stage_id": sid}

    if sid in {"assert_and_deliver"}:
        if any(
            k in joined
            for k in (
                "assertions",
                "assert_",
                "passed",
                "failed",
                "业务断言",
                "assert_task_postcondition",
                "test_result",
            )
        ):
            return {"ok": True, "reason": "assert_evidence", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "assert_incomplete", "stage_id": sid}

    if sid in {"cleanup_data"}:
        if any(
            k in joined
            for k in (
                "cleanup_status",
                "cleanup_errors",
                "后置",
                "清理",
                "posterior_sql",
                "deleted",
            )
        ) or ids.get("cleanup_status"):
            return {"ok": True, "reason": "cleanup_done", "stage_id": sid, "ids": ids}
        # 无清理动作也允许推进结束（避免卡死）
        if "无需清理" in joined or "no cleanup" in joined.lower():
            return {"ok": True, "reason": "cleanup_noop", "stage_id": sid}
        return {"ok": False, "reason": "cleanup_incomplete", "stage_id": sid}

    # 浏览器常见节点
    if sid in {
        "fetch_conditions",
        "check_executable",
        "add_to_cart",
        "submit_order",
        "upload",
        "deliver",
    }:
        if joined.strip():
            # 有工具输出即视为有进展；细粒度仍由既有 browser_interact 逻辑兜底
            return {"ok": True, "reason": "browser_node_output", "stage_id": sid}
        return {"ok": False, "reason": "no_tool_output", "stage_id": sid}

    return {"ok": False, "reason": "unknown_orch_node", "stage_id": sid}


def merge_ids_into_facts(
    facts: Dict[str, Any],
    ids: Dict[str, Any],
    *,
    skill_name: str = "",
    tool_succeeded: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """把执行标识与对象 ID 写入 facts；返回 (facts, note)。"""
    out = dict(facts or {})
    note = ""
    if ids.get("testcase_id") or ids.get("case_id"):
        case_id = ids.get("testcase_id") or ids.get("case_id")
        source = "ui_testcase"
        if ids.get("task_execution_id") and not ids.get("record_id"):
            source = "api_interface_case"
        out["case_ref"] = make_case_ref(
            source_type=source,
            case_id=case_id,
            project_id=(out.get("case_ref") or {}).get("project_id")
            if isinstance(out.get("case_ref"), dict)
            else None,
        )
    if any(k in ids for k in ("record_id", "execution_id", "task_execution_id")):
        out = bind_execution_ref(
            out,
            execution_id=ids.get("execution_id") or ids.get("task_execution_id"),
            record_id=ids.get("record_id"),
            case_ref=out.get("case_ref")
            if isinstance(out.get("case_ref"), dict)
            else None,
            status="running",
            skill_name=skill_name,
        )
        if ids.get("task_execution_id"):
            out["task_execution_id"] = ids.get("task_execution_id")
    for k in (
        "object_id",
        "prepared_object_ids",
        "parametrize_results",
        "cleanup_status",
        "cleanup_errors",
    ):
        if k in ids and ids[k] not in (None, ""):
            out[k] = ids[k]
    test_result = ids.get("test_result")
    if not test_result and ids.get("status") in {
        "success",
        "failed",
        "passed",
        "error",
        "skipped",
    }:
        # UI 执行器 success≠passed；映射为粗粒度
        st = ids.get("status")
        test_result = (
            "passed"
            if st in {"success", "passed"}
            else ("failed" if st == "failed" else st)
        )
    if test_result:
        out = update_execution_status(out, status="completed", test_result=test_result)
    note = call_success_is_not_test_pass(tool_succeeded, out.get("test_result"))
    return out, note


def expand_commission_skills(commission: Any, skills: Set[str]) -> None:
    if commission is None or not skills:
        return
    try:
        commission.primary_skills = frozenset(
            set(commission.primary_skills or []) | set(skills)
        )
        commission.granted_auxiliary = set(commission.granted_auxiliary or []) | set(
            skills
        )
    except Exception:
        pass


def advance_orchestrator_on_task(
    task,
    *,
    tool_results: Optional[List[str]] = None,
    force: bool = False,
    failed: bool = False,
) -> Dict[str, Any]:
    """编排路径阶段推进：同步 TaskPlan、扩权下一节点 Skill、写回 facts。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask
    from orchestrator_integration.agent_runtime import append_event

    facts = dict(task.facts or {})
    raw_plan = facts.get("task_plan")
    if not isinstance(raw_plan, dict):
        return {
            "advanced": False,
            "current_stage": task.current_stage or "",
            "reason": "no_task_plan",
        }
    plan = plan_from_dict(raw_plan)
    # 与 DB current_stage 对齐
    cur_stage = task.current_stage or plan.current_node
    if cur_stage and cur_stage != plan.current_node:
        for n in plan.nodes:
            if n.node_id == cur_stage:
                plan.current_node = cur_stage
                if n.status not in {NODE_DONE, NODE_FAILED}:
                    n.status = NODE_RUNNING
                break

    cur = plan.current()
    if cur is None:
        return {
            "advanced": False,
            "current_stage": plan.current_node or "",
            "reason": "no_current_node",
        }

    verdict = orch_node_acceptance(cur.node_id, tool_results)
    # 合并本轮提取到的 ids 进 facts（推进前后都登记）
    ids = verdict.get("ids") or extract_execution_ids(
        "\n".join(str(x) for x in (tool_results or []) if x)
    )
    facts, note = merge_ids_into_facts(facts, ids, tool_succeeded=True)

    if not force and not failed and not verdict.get("ok"):
        # 仍写回已提取 facts（标识尽早绑定）
        AgentTask.objects.filter(pk=task.pk).update(
            facts=facts, updated_at=timezone.now()
        )
        task.facts = facts
        return {
            "advanced": False,
            "current_stage": cur.node_id,
            "reason": "stage_not_accepted",
            "verdict": verdict,
            "note": note,
        }

    from_id = cur.node_id
    # 推进前：若下一节点缺少 input_refs，硬拦（force 可越过）
    if verdict.get("ok") and not failed and not force:
        try:
            from orchestrator_integration.task_orchestrator.gates import (
                check_input_refs,
            )

            probe = advance_plan_node(
                plan_from_dict(plan.to_dict()),
                accepted=True,
                failed=False,
            )
            nxt_probe = probe.current()
            if nxt_probe and nxt_probe.node_id != from_id:
                gate_err = check_input_refs(probe, nxt_probe, facts)
                if gate_err:
                    AgentTask.objects.filter(pk=task.pk).update(
                        facts=facts, updated_at=timezone.now()
                    )
                    task.facts = facts
                    return {
                        "advanced": False,
                        "current_stage": from_id,
                        "reason": "input_refs_blocked",
                        "verdict": verdict,
                        "gate_error": gate_err,
                        "note": note,
                    }
        except Exception:
            pass

    plan = advance_plan_node(
        plan, accepted=bool(verdict.get("ok") or force), failed=failed
    )
    activated = set(facts.get("activated_fallbacks") or [])
    mount = apply_orchestrator_mount(plan, activated_fallbacks=activated)
    facts["task_plan"] = plan.to_dict()
    facts["strategy_version"] = STRATEGY_VERSION
    facts["activated_fallbacks"] = sorted(activated | set(mount.activated_fallbacks))
    completed = set(facts.get("completed_stage_ids") or [])
    if from_id and (verdict.get("ok") or force) and not failed:
        completed.add(from_id)
    if failed:
        failed_ids = set(facts.get("failed_stage_ids") or [])
        failed_ids.add(from_id)
        facts["failed_stage_ids"] = sorted(failed_ids)
        # 主流程失败仍可能转到 cleanup
        if plan.current_node == "cleanup_data" and plan.status == TASK_RUNNING:
            facts["main_flow_failed"] = True
    facts["completed_stage_ids"] = sorted(completed)

    new_version = int(task.plan_version or 1) + 1
    budget = dict(task.budget or {})
    if plan.status == TASK_RUNNING and plan.current_node != from_id:
        done = int(budget.get("completed_business_steps") or 0) + 1
        expected = int(budget.get("expected_business_steps") or plan.estimated_nodes or 0)
        budget["completed_business_steps"] = min(done, expected) if expected else done

    auth = dict(task.auth_scope or {})
    auth.update(mount.to_auth_scope_patch())

    nxt = plan.current_node
    advanced = nxt != from_id or plan.status in {TASK_FAILED, "done"}
    # 「推进到下一节点」才算 advanced；同节点等待不算
    node_changed = nxt != from_id

    AgentTask.objects.filter(pk=task.pk).update(
        current_stage=nxt or from_id,
        plan_version=new_version if node_changed else task.plan_version,
        budget=budget,
        facts=facts,
        auth_scope=auth,
        status="failed"
        if plan.status == TASK_FAILED
        else ("completed" if plan.status == "done" else task.status),
        updated_at=timezone.now(),
    )
    task.current_stage = nxt or from_id
    if node_changed:
        task.plan_version = new_version
    task.budget = budget
    task.facts = facts
    task.auth_scope = auth

    remount_skills = set(mount.skill_allowlist)
    evt = None
    if node_changed:
        evt = append_event(
            task,
            "stage_changed",
            status="running" if plan.status == TASK_RUNNING else plan.status,
            payload={
                "from_stage": from_id,
                "to_stage": nxt,
                "plan_version": task.plan_version,
                "verdict": verdict,
                "remount_skills": sorted(remount_skills),
                "orchestrator": True,
                "main_flow_failed": bool(facts.get("main_flow_failed")),
                "note": note,
            },
        )
    return {
        "advanced": node_changed,
        "from_stage": from_id,
        "current_stage": nxt or from_id,
        "plan_version": task.plan_version,
        "verdict": verdict,
        "remount_skills": sorted(remount_skills),
        "orchestrator": True,
        "plan_status": plan.status,
        "event": evt,
        "note": note,
        "reason": "orch_advanced" if node_changed else "orch_same_node",
    }


def apply_remount_to_context(remount_skills: List[str] | Set[str]) -> None:
    """同轮扩权：更新 commission + telemetry（execute_skill_script 以 commission 为准）。"""
    skills = {s for s in (remount_skills or []) if s}
    if not skills:
        return
    try:
        from orchestrator_integration.telemetry_context import get_telemetry_context

        ctx = get_telemetry_context()
        expand_commission_skills(ctx.commission, skills)
        ctx.granted_skills = set(ctx.granted_skills or []) | skills
        if ctx.orchestrator_mount is not None:
            try:
                ctx.orchestrator_mount.skill_allowlist = set(
                    ctx.orchestrator_mount.skill_allowlist or []
                ) | skills
            except Exception:
                pass
    except Exception:
        pass
