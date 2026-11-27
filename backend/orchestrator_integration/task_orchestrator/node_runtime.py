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
    (
        "order_sn",
        re.compile(
            r'"order_sn"\s*:\s*"([^"]+)"|order_sn\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
    (
        "cart_id",
        re.compile(
            r'"cart_id"\s*:\s*"([^"]+)"|cart_id\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
    (
        "cart_item_id",
        re.compile(
            r'"cart_item_id"\s*:\s*"([^"]+)"|cart_item_id\s*[:=]\s*([A-Za-z0-9_-]+)',
            re.I,
        ),
    ),
)

# 节点 output_keys / 跨工具事实字段（F07／F12）
_FACT_VALUE_KEYS = (
    "record_id",
    "execution_id",
    "task_execution_id",
    "testcase_id",
    "case_id",
    "object_id",
    "prepared_object_ids",
    "parametrize_results",
    "test_result",
    "cleanup_status",
    "cleanup_errors",
    "order_sn",
    "cart_id",
    "cart_item_id",
    "entry_url",
    "product_url",
    "product_hint",
    "test_image",
    "extracted_vars",
    "upload_ok",
    "preview_ok",
    "env_base_url",
    "page_ok",
    "amount",
    "rules",
    "failed_steps",
    "passed_steps",
)

_TEST_OUTCOME_VALUES = frozenset(
    {"passed", "failed", "error", "skipped", "pass", "fail"}
)
_TOOL_CALL_SUCCESS = frozenset({"success", "succeeded", "ok", "true"})
_TOOL_CALL_FAILURE = frozenset({"failed", "error", "false"})


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


def _merge_blob_layers(blob: Dict[str, Any]) -> Dict[str, Any]:
    """顶层 + data.* 合并；内层测试态优先于外层调用态（F02／F12）。"""
    if not isinstance(blob, dict):
        return {}
    merged = dict(blob)
    nested = blob.get("data")
    if isinstance(nested, dict):
        for k, v in nested.items():
            if v in (None, ""):
                continue
            if k == "status":
                # 内层 status 记为 test/data 态，外层保留为 tool 调用态
                merged.setdefault("inner_status", v)
                if str(v).lower() in _TEST_OUTCOME_VALUES or str(v).lower() in {
                    "failed",
                    "error",
                    "running",
                    "completed",
                }:
                    merged["inner_status"] = v
            elif k not in merged or merged[k] in (None, ""):
                merged[k] = v
            elif k in _FACT_VALUE_KEYS:
                merged[k] = v
    return merged


def extract_execution_ids(text: str) -> Dict[str, Any]:
    blob = _merge_blob_layers(extract_structured_blob(text))
    out: Dict[str, Any] = {}
    for key in _FACT_VALUE_KEYS:
        if key in blob and blob[key] not in (None, ""):
            out[key] = blob[key]
    # 外层调用 status 与内层测试 status 分离
    if "status" in blob and blob["status"] not in (None, ""):
        out["status"] = blob["status"]
    if "inner_status" in blob and blob["inner_status"] not in (None, ""):
        out["inner_status"] = blob["inner_status"]
    for key, pat in _ID_PATTERNS:
        if key not in out:
            m = pat.search(text or "")
            if m:
                val = next((g for g in m.groups() if g is not None), None)
                if val is not None:
                    out[key] = (
                        int(val)
                        if key.endswith("_id") and str(val).isdigit()
                        else val
                    )
    return out


def _looks_like_tool_failure(joined: str, ids: Dict[str, Any]) -> bool:
    """结构化失败：外层/内层 status=failed|error，或明确 error 载荷。"""
    st = str(ids.get("inner_status") or ids.get("status") or "").lower()
    if st in _TOOL_CALL_FAILURE or st in {"failed", "error"}:
        return True
    if re.search(
        r'(?i)"status"\s*:\s*"(failed|error)"|"ok"\s*:\s*false|"success"\s*:\s*false',
        joined or "",
    ):
        return True
    if re.search(r'(?i)"error"\s*:\s*"[^"]{3,}"', joined or "") and not re.search(
        r'(?i)"status"\s*:\s*"(success|succeeded|passed)"', joined or ""
    ):
        # 纯错误对象且无成功态
        if "case_id" not in (joined or "") and "testcase_id" not in (joined or ""):
            return True
        if re.search(r"(?i)failed:|service unavailable|assertions missing", joined or ""):
            return True
    return False


def _truthy_flag(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val in (None, ""):
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "ok", "passed", "success"}


def _latest_attempt(tool_results: Optional[List[str]]) -> str:
    """取最近一次工具结果；二层备用成功后不应被历史失败永久阻断（X03）。"""
    attempts = [str(x) for x in (tool_results or []) if x]
    return attempts[-1] if attempts else ""


def _evaluate_assertions_payload(text: str) -> Optional[bool]:
    """逐项核对 assertions。

    Returns:
        True: 存在非空断言且全部 passed
        False: 有 assertions 字段但为空，或任一项失败
        None: 无 assertions 字段（改用其他证据）
    """
    blob = _merge_blob_layers(extract_structured_blob(text or ""))
    if "assertions" not in blob:
        # 顶层未出现时再扫原文，避免误把别的数组当断言
        if not re.search(r'(?i)"assertions"\s*:', text or ""):
            return None
        return False
    assertions = blob.get("assertions")
    if not isinstance(assertions, list):
        return False
    if len(assertions) == 0:
        return False  # X02：空数组不算完成核验
    for item in assertions:
        if not isinstance(item, dict):
            return False
        if "passed" in item:
            if not _truthy_flag(item.get("passed")):
                return False  # X01
            continue
        if "ok" in item:
            if not _truthy_flag(item.get("ok")):
                return False
            continue
        result = str(item.get("result") or item.get("status") or "").lower()
        if result in {"failed", "fail", "error", "false"}:
            return False
        if result not in {"passed", "pass", "success", "ok", "true"}:
            # 无明确通过字段 → 不完整
            return False
    return True


def orch_node_acceptance(
    stage_id: str, tool_results: Optional[List[str]] = None
) -> Dict[str, Any]:
    """编排节点专用验收（可推进则 ok）。按最近一次有效尝试的结构化结果判定。"""
    attempts = [str(x) for x in (tool_results or []) if x]
    latest = attempts[-1] if attempts else ""
    # 历史文本仅作诊断；成败与 ID 以最近一次尝试为准（X03）
    joined = latest
    sid = stage_id or ""
    ids = extract_execution_ids(latest)

    if sid in {"query_cases"}:
        if _looks_like_tool_failure(latest, ids):
            return {"ok": False, "reason": "query_failed", "stage_id": sid, "ids": ids}
        # 必须有真实 case 标识；动作名出现在错误文案中不算（F04）
        has_case = bool(ids.get("testcase_id") or ids.get("case_id"))
        if not has_case:
            # JSON 列表成功形态：[{"case_id": N, "case_name": ...}]
            has_case = bool(
                re.search(r'"case_id"\s*:\s*\d+|"testcase_id"\s*:\s*\d+', latest or "")
            )
        if has_case:
            return {"ok": True, "reason": "case_ref_found", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "missing_case_ref", "stage_id": sid}

    if sid in {"prepare_data", "api_prepare_or_run"}:
        if _looks_like_tool_failure(latest, ids):
            return {
                "ok": False,
                "reason": "prepare_failed",
                "stage_id": sid,
                "ids": ids,
            }
        if (
            ids.get("object_id")
            or ids.get("prepared_object_ids")
            or ids.get("extracted_vars")
            or ids.get("task_execution_id")
            or ids.get("execution_id")
            or any(
                k in latest
                for k in ("prepared_object_ids", "造数", "准备成功", "front_sql")
            )
        ):
            return {"ok": True, "reason": "prepare_evidence", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "prepare_incomplete", "stage_id": sid}

    if sid in {"run_or_browser", "execute", "execute_testcase"}:
        if ids.get("record_id") or ids.get("execution_id") or ids.get(
            "task_execution_id"
        ):
            return {
                "ok": True,
                "reason": "run_execution_bound",
                "stage_id": sid,
                "ids": ids,
            }
        if any(
            k in latest
            for k in ("parametrize_results", "终态", "u_case_result")
        ):
            return {
                "ok": True,
                "reason": "run_execution_bound",
                "stage_id": sid,
                "ids": ids,
            }
        return {"ok": False, "reason": "missing_execution_ref", "stage_id": sid}

    if sid in {"assert_and_deliver"}:
        if _looks_like_tool_failure(latest, ids):
            return {
                "ok": False,
                "reason": "assert_failed",
                "stage_id": sid,
                "ids": ids,
            }
        assertion_verdict = _evaluate_assertions_payload(latest)
        if assertion_verdict is False:
            return {
                "ok": False,
                "reason": "assert_failed",
                "stage_id": sid,
                "ids": ids,
            }
        if assertion_verdict is True:
            return {
                "ok": True,
                "reason": "assert_evidence",
                "stage_id": sid,
                "ids": ids,
            }
        tr = str(ids.get("test_result") or ids.get("inner_status") or "").lower()
        if tr in {"passed", "pass"}:
            return {"ok": True, "reason": "assert_evidence", "stage_id": sid, "ids": ids}
        if re.search(
            r'(?i)"ok"\s*:\s*true|"test_result"\s*:\s*"passed"|'
            r'assert_task_postcondition[^\n]{0,120}"ok"\s*:\s*true',
            latest or "",
        ) and not re.search(r"(?i)assertions missing", latest or ""):
            return {"ok": True, "reason": "assert_evidence", "stage_id": sid, "ids": ids}
        return {"ok": False, "reason": "assert_incomplete", "stage_id": sid, "ids": ids}

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
        if "无需清理" in joined or "no cleanup" in joined.lower():
            return {"ok": True, "reason": "cleanup_noop", "stage_id": sid}
        return {"ok": False, "reason": "cleanup_incomplete", "stage_id": sid}

    if sid in {"customize_upload", "upload"}:
        if _looks_like_tool_failure(joined, ids):
            return {"ok": False, "reason": "upload_failed", "stage_id": sid, "ids": ids}
        upload_ok = _truthy_flag(ids.get("upload_ok")) or bool(
            re.search(r'(?i)"upload_ok"\s*:\s*true', joined or "")
        )
        preview_ok = _truthy_flag(ids.get("preview_ok")) or bool(
            re.search(r'(?i)"preview_ok"\s*:\s*true', joined or "")
        )
        if upload_ok and preview_ok:
            return {
                "ok": True,
                "reason": "upload_preview_ok",
                "stage_id": sid,
                "ids": ids,
            }
        return {
            "ok": False,
            "reason": "upload_incomplete",
            "stage_id": sid,
            "ids": ids,
        }

    if sid in {"add_to_cart"}:
        if _looks_like_tool_failure(joined, ids):
            return {
                "ok": False,
                "reason": "add_to_cart_failed",
                "stage_id": sid,
                "ids": ids,
            }
        if ids.get("cart_id") or ids.get("cart_item_id"):
            return {
                "ok": True,
                "reason": "cart_bound",
                "stage_id": sid,
                "ids": ids,
            }
        return {
            "ok": False,
            "reason": "missing_cart_id",
            "stage_id": sid,
            "ids": ids,
        }

    if sid in {"submit_order"}:
        if _looks_like_tool_failure(joined, ids):
            return {
                "ok": False,
                "reason": "submit_order_failed",
                "stage_id": sid,
                "ids": ids,
            }
        if ids.get("order_sn"):
            return {"ok": True, "reason": "order_bound", "stage_id": sid, "ids": ids}
        return {
            "ok": False,
            "reason": "missing_order_sn",
            "stage_id": sid,
            "ids": ids,
        }

    if sid in {
        "fetch_conditions",
        "check_executable",
        "deliver",
    }:
        if _looks_like_tool_failure(joined, ids):
            return {
                "ok": False,
                "reason": "browser_node_failed",
                "stage_id": sid,
                "ids": ids,
            }
        if sid == "fetch_conditions" and (
            ids.get("entry_url")
            or ids.get("product_url")
            or "entry_url" in joined
            or "http" in joined.lower()
        ):
            return {
                "ok": True,
                "reason": "fetch_conditions_ok",
                "stage_id": sid,
                "ids": ids,
            }
        if sid == "check_executable" and (
            _truthy_flag(ids.get("page_ok"))
            or ids.get("product_url")
            or "page_ok" in joined
        ):
            return {
                "ok": True,
                "reason": "check_executable_ok",
                "stage_id": sid,
                "ids": ids,
            }
        if joined.strip():
            return {
                "ok": True,
                "reason": "browser_node_output",
                "stage_id": sid,
                "ids": ids,
            }
        return {"ok": False, "reason": "no_tool_output", "stage_id": sid}

    return {"ok": False, "reason": "unknown_orch_node", "stage_id": sid}


def resolve_test_result_from_ids(ids: Dict[str, Any]) -> Optional[str]:
    """仅从明确测试态推导 passed/failed；外层 success 不算通过（F01／F02）。"""

    def _failed_steps() -> bool:
        try:
            return int(ids.get("failed_steps") or 0) > 0
        except (TypeError, ValueError):
            return False

    explicit = ids.get("test_result")
    if explicit not in (None, ""):
        val = str(explicit).lower()
        if _failed_steps():
            return "failed"
        if val in {"passed", "pass"}:
            return "passed"
        if val in {"failed", "fail"}:
            return "failed"
        if val == "error":
            return "error"
        if val == "skipped":
            return "skipped"
        # UI 执行器常把 test_result/status 写成 success：不等于 passed
        return None

    inner = str(ids.get("inner_status") or "").lower()
    if _failed_steps() or inner in {"failed", "fail"}:
        return "failed"
    if inner in {"passed", "pass"}:
        return "passed"
    if inner == "error":
        return "error"
    if inner == "skipped":
        return "skipped"
    # 外层 status=success／failed：不映射为测试通过
    return None


def merge_ids_into_facts(
    facts: Dict[str, Any],
    ids: Dict[str, Any],
    *,
    skill_name: str = "",
    tool_succeeded: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """把执行标识与节点输出写入 facts；调用 success ≠ 测试 passed。"""
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

    for k in _FACT_VALUE_KEYS:
        if k in {
            "testcase_id",
            "case_id",
            "record_id",
            "execution_id",
            "task_execution_id",
            "test_result",
            "status",
            "failed_steps",
            "passed_steps",
        }:
            continue
        if k in ids and ids[k] not in (None, ""):
            out[k] = ids[k]
            # 结构化溯源（F12）
            from orchestrator_integration.task_orchestrator.execution_refs import (
                _now as _fact_now,
            )

            sources = dict(out.get("fact_sources") or {})
            sources[k] = {
                "value": ids[k],
                "skill_name": skill_name or "",
                "updated_at": _fact_now(),
            }
            out["fact_sources"] = sources

    test_result = resolve_test_result_from_ids(ids)
    if test_result:
        out = update_execution_status(
            out, status="completed", test_result=test_result
        )
    note = call_success_is_not_test_pass(tool_succeeded, out.get("test_result"))
    outer = str(ids.get("status") or "").lower()
    if tool_succeeded or outer in _TOOL_CALL_SUCCESS:
        if not test_result:
            note = note or (
                "工具调用 succeeded 仅表示受理/调用成功，不是测试通过。"
                "须读取本次 execution_id 对应断言与业务终态后再判定 passed/failed。"
            )
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
    """同轮扩权：更新 commission + telemetry（execute_skill_script 以 commission 为准）。

    同时持久化到任务 auth_scope/facts，避免同轮 granted 后门禁仍读空主类。
    """
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
                ctx.orchestrator_mount.activated_fallbacks = set(
                    getattr(ctx.orchestrator_mount, "activated_fallbacks", None)
                    or []
                ) | skills
            except Exception:
                pass
        # 写入任务有效授权（同轮后续调用 + 跨轮恢复）
        try:
            from orchestrator_integration.models import AgentTask
            from orchestrator_integration.task_orchestrator.tool_auth_orchestration import (
                persist_granted_skills,
            )

            task = None
            if getattr(ctx, "runtime_task_id", None):
                task = AgentTask.objects.filter(pk=ctx.runtime_task_id).first()
            if task is not None:
                persist_granted_skills(task, skills, commission=ctx.commission)
        except Exception:
            pass
    except Exception:
        pass
