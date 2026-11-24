"""跨工具事实与本次执行标识（R03 / R11 / D06）。

execution_id／record_id 发起即绑定；不得用「该用例最新记录」替代本次运行。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

# 用例来源类型（§2.1）— 标识命名空间不可混用
CASE_SOURCE_PLATFORM = "platform_testcase"  # loca-stude 功能用例
CASE_SOURCE_UI = "ui_testcase"  # UiTestCase
CASE_SOURCE_API_SCENARIO = "api_scenario"
CASE_SOURCE_API_INTERFACE = "api_interface_case"
CASE_SOURCE_TEMP_BROWSER = "temp_browser_script"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_case_ref(
    *,
    source_type: str,
    case_id: Any,
    project_id: Any = None,
    service: str = "",
    env: str = "",
) -> Dict[str, Any]:
    return {
        "source_type": source_type,
        "case_id": case_id,
        "project_id": project_id,
        "service": service,
        "env": env,
        "namespace": f"{source_type}:{project_id}:{case_id}",
    }


def bind_execution_ref(
    facts: Dict[str, Any],
    *,
    execution_id: Any = None,
    record_id: Any = None,
    operation_id: str = "",
    case_ref: Optional[Dict[str, Any]] = None,
    status: str = "submitted",
    skill_name: str = "",
) -> Dict[str, Any]:
    """写入本次执行标识；后续 wait/query/report 必须带同一标识。

    绑定新的 execution_id／record_id 时：归档旧 execution_ref，清空顶层
    test_result，新执行从 submitted/running、未判定结果开始（F08）。
    """
    out = dict(facts or {})
    prev = dict(out.get("execution_ref") or {})
    prev_eid = prev.get("execution_id")
    prev_rid = prev.get("record_id")
    rebinding_different = (
        execution_id is not None
        and prev_eid not in (None, "")
        and str(prev_eid) != str(execution_id)
    ) or (
        record_id is not None
        and prev_rid not in (None, "")
        and str(prev_rid) != str(record_id)
    )
    is_fresh = (not prev) or (prev_eid in (None, "") and prev_rid in (None, ""))
    is_new_binding = bool(is_fresh or rebinding_different)
    if rebinding_different and prev:
        history = list(out.get("execution_history") or [])
        history.append(prev)
        out["execution_history"] = history[-10:]
    if is_new_binding:
        out.pop("test_result", None)

    ref = {
        "execution_id": execution_id if execution_id is not None else prev_eid,
        "record_id": record_id if record_id is not None else prev_rid,
        "operation_id": operation_id or prev.get("operation_id") or "",
        "case_ref": case_ref or out.get("case_ref") or prev.get("case_ref"),
        "status": status,  # submitted|running|completed|cancelled|unknown
        "skill_name": skill_name or prev.get("skill_name") or "",
        "bound_at": _now() if is_new_binding else (prev.get("bound_at") or _now()),
    }
    # 新绑定不得继承历史 passed；同绑定更新可保留已有 test_result
    if is_new_binding:
        ref["test_result"] = None
    else:
        ref["test_result"] = prev.get("test_result")
        if ref["test_result"] is None:
            ref["test_result"] = out.get("test_result")
    out["execution_ref"] = ref
    if case_ref:
        out["case_ref"] = case_ref
    if execution_id is not None:
        out["execution_id"] = execution_id
    if record_id is not None:
        out["record_id"] = record_id
    return out


def update_execution_status(
    facts: Dict[str, Any],
    *,
    status: Optional[str] = None,
    test_result: Optional[str] = None,
    raw: Any = None,
) -> Dict[str, Any]:
    out = dict(facts or {})
    ref = dict(out.get("execution_ref") or {})
    if status:
        ref["status"] = status
    if test_result:
        ref["test_result"] = test_result
        out["test_result"] = test_result
    if raw is not None:
        ref["last_raw_keys"] = (
            sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        )
    ref["updated_at"] = _now()
    out["execution_ref"] = ref
    return out


def assert_same_execution(
    facts: Dict[str, Any],
    *,
    execution_id: Any = None,
    record_id: Any = None,
) -> Optional[str]:
    """查询结果若与本次绑定不一致则返回错误说明。"""
    ref = (facts or {}).get("execution_ref") or {}
    if execution_id is not None and ref.get("execution_id") not in (None, ""):
        if str(ref.get("execution_id")) != str(execution_id):
            return (
                f"执行标识不一致：绑定 execution_id={ref.get('execution_id')}，"
                f"查询得到 {execution_id}。禁止用他次/最新记录交差。"
            )
    if record_id is not None and ref.get("record_id") not in (None, ""):
        if str(ref.get("record_id")) != str(record_id):
            return (
                f"记录标识不一致：绑定 record_id={ref.get('record_id')}，"
                f"查询得到 {record_id}。禁止串用同用例其他运行结果。"
            )
    return None


def call_success_is_not_test_pass(tool_succeeded: bool, test_result: Optional[str]) -> str:
    """R03：外层 succeeded 不能直接变成测试 passed。"""
    if tool_succeeded and test_result in (None, "", "unknown"):
        return (
            "工具调用 succeeded 仅表示受理/调用成功，不是测试通过。"
            "须读取本次 execution_id 对应断言与业务终态后再判定 passed/failed。"
        )
    return ""
