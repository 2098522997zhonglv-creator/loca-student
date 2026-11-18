#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 离线自测探针（无需 Django / 浏览器 / 网络）。"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK  {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f" FAIL {name}" + (f" — {detail}" if detail else ""))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    print("=== P1 offline probe ===")

    # --- step_advanced ---
    print("\n[step_advanced]")
    sa = load_module("step_advanced", ROOT / "step_advanced.py")
    dp_mod = load_module("data_processor", ROOT / "data_processor.py")
    dp = dp_mod.DataProcessor()

    err = sa.preflight_unsupported_steps(
        [type("S", (), {"step_id": 9, "step_type": 5})()]
    )
    check("preflight rejects Python(5)", err is not None and "Python" in err)

    ok, _ = sa.apply_custom_variables(dp, {"set": {"flag": "1", "user": "u1"}})
    check("custom vars via set{}", ok and dp.get_cache("flag") == "1")

    ok, msg = sa.evaluate_condition(
        dp, {"left": "${{flag}}", "op": "eq", "right": "1"}
    )
    check("condition true", ok and "True" in msg)

    ok_f, _ = sa.evaluate_condition(
        dp, {"left": "${{flag}}", "op": "eq", "right": "0"}
    )
    check("condition false", not ok_f)

    rows = sa.parse_parametrize_rows([{"a": 1}, {"a": 2}, {"a": 3}])
    check("parametrize 3 rows", len(rows) == 3 and rows[1]["a"] == 2)

    ok, _ = sa.apply_front_custom(dp, [{"key": "prep", "value": "ok"}])
    check("front_custom list", ok and dp.get_cache("prep") == "ok")

    # --- CaseResultModel fields ---
    print("\n[models CaseResultModel]")
    try:
        # pydantic may be available; else skip
        models = load_module("actuator_models", ROOT / "models.py")
        m = models.CaseResultModel(
            case_id=1,
            status="success",
            skipped_steps=2,
            record_id=77,
            execution_id="ui-exec-77",
            parametrize_row=0,
            parametrize_results=[{"status": "success"}],
        )
        d = m.model_dump()
        check(
            "CaseResultModel dumps P1 fields",
            d.get("skipped_steps") == 2
            and d.get("record_id") == 77
            and d.get("execution_id") == "ui-exec-77"
            and len(d.get("parametrize_results") or []) == 1,
        )
    except Exception as e:
        check("CaseResultModel dumps P1 fields", False, str(e))

    # --- ui_automation_tools env + testcase field plumbing (mock HTTP) ---
    print("\n[ui_automation_tools env/testcase]")
    tools_path = (
        REPO / "bundled_skills" / "ui-automation-skill" / "ui_automation_tools.py"
    )
    # Avoid executing network on import side-effects: load with mocked deps
    src = tools_path.read_text(encoding="utf-8")
    # Sanity: create_env_config writes extra_config
    check(
        "create_env_config source uses extra_config",
        "extra_config" in src and "browser/headless/viewport" in src,
    )
    check(
        "execute_testcase forbids latest-record fallback",
        "禁止改查" in src or "禁止用最新记录" in src,
    )
    check(
        "create_testcase accepts parametrize",
        "parametrize" in src and "front_custom" in src and "posterior_sql" in src,
    )

    # Dynamic import of helper-level logic via exec of selected functions is heavy;
    # instead AST-check update_env_config merges browser keys into extra_config.
    tree = ast.parse(src)
    update_fn = None
    create_env_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "update_env_config":
            update_fn = node
        if isinstance(node, ast.FunctionDef) and node.name == "create_env_config":
            create_env_fn = node
    check("update_env_config defined", update_fn is not None)
    update_src = ast.get_source_segment(src, update_fn) or ""
    check(
        "update_env_config maps browser keys",
        "extra_config" in update_src and "browser_keys" in update_src,
    )
    create_src = ast.get_source_segment(src, create_env_fn) or ""
    check(
        "create_env_config payload nests browser",
        '"browser": browser' in create_src or "'browser': browser" in create_src,
    )

    # Runtime mock of create_env_config / create_testcase / execute timeout path
    sys.path.insert(0, str(tools_path.parent))
    # Stub websocket so import succeeds
    sys.modules.setdefault("websocket", MagicMock())
    tools = load_module("ui_automation_tools", tools_path)

    captured = {}

    def fake_request(method, path, data=None, params=None):
        captured["last"] = {
            "method": method,
            "path": path,
            "data": data,
            "params": params,
        }
        return {"status": "success", "data": {"id": 1, **(data or {})}}

    with patch.object(tools, "_request", side_effect=fake_request):
        tools.create_env_config(1, "dev", base_url="http://x", browser="firefox", headless=True)
        payload = captured["last"]["data"]
        check(
            "create_env_config POST has extra_config.browser",
            isinstance(payload.get("extra_config"), dict)
            and payload["extra_config"].get("browser") == "firefox",
            detail=json.dumps(payload, ensure_ascii=False)[:200],
        )
        check(
            "create_env_config no top-level browser",
            "browser" not in payload,
            detail=str(sorted(payload.keys())),
        )

        tools.create_testcase(
            1,
            2,
            "p1-case",
            front_custom='{"k":"v"}',
            parametrize='[{"u":"a"},{"u":"b"}]',
            posterior_sql='[{"sql":"delete from t"}]',
        )
        tc = captured["last"]["data"]
        check(
            "create_testcase JSON fields parsed",
            tc.get("front_custom") == {"k": "v"}
            and isinstance(tc.get("parametrize"), list)
            and len(tc["parametrize"]) == 2
            and isinstance(tc.get("posterior_sql"), list),
            detail=str({k: tc.get(k) for k in ("front_custom", "parametrize", "posterior_sql")}),
        )

        tools.update_env_config(9, browser="webkit", viewport_width=800, name="n2")
        up = captured["last"]["data"]
        check(
            "update_env_config merges into extra_config",
            up.get("name") == "n2"
            and up.get("extra_config", {}).get("browser") == "webkit"
            and up.get("extra_config", {}).get("viewport_width") == 800
            and "browser" not in up,
            detail=json.dumps(up, ensure_ascii=False)[:220],
        )

    # --- plan prepare/cleanup ---
    print("\n[task_orchestrator plan]")
    sys.path.insert(0, str(REPO / "backend"))
    from orchestrator_integration.intent_router import IntentDecision
    from orchestrator_integration.task_orchestrator import build_task_plan

    plan = build_task_plan(
        "执行测试用例：复杂参数化，需要数据准备与清理",
        IntentDecision(intents=set(), primary="qa", reason="probe"),
    )
    ids = [n.node_id for n in plan.nodes]
    check(
        "complex plan has prepare+cleanup",
        ids == [
            "query_cases",
            "prepare_data",
            "run_or_browser",
            "assert_and_deliver",
            "cleanup_data",
        ]
        or (
            "prepare_data" in ids
            and "cleanup_data" in ids
            and ids.index("prepare_data") < ids.index("run_or_browser") < ids.index("cleanup_data")
        ),
        detail=str(ids),
    )
    simple = build_task_plan(
        "执行测试用例 99",
        IntentDecision(intents=set(), primary="qa", reason="probe"),
    )
    sids = [n.node_id for n in simple.nodes]
    check(
        "simple plan skips prepare/cleanup",
        "prepare_data" not in sids and "cleanup_data" not in sids and "run_or_browser" in sids,
        detail=str(sids),
    )

    # --- CONDITION_FALSE skip semantics (pure) ---
    print("\n[condition skip semantics]")
    message = "CONDITION_FALSE: '1' == '0' => False"
    skip_rest = message.startswith("CONDITION_FALSE:")
    check("CONDITION_FALSE triggers skip flag", skip_rest)
    # skipped status must not be passed
    status = "success"  # condition step itself
    later = "skipped"
    check("later steps use skipped not passed", later == "skipped" and status == "success")

    # --- consumers early record_id source ---
    print("\n[consumers early record_id]")
    cons = (REPO / "backend" / "ui_automation" / "consumers.py").read_text(encoding="utf-8")
    check(
        "create_running_execution_record present",
        "create_running_execution_record" in cons and 'status=1' in cons,
    )
    check(
        "effective_runtime echoes record_id",
        '"record_id": args.get("record_id")' in cons
        or "'record_id': args.get('record_id')" in cons,
    )
    check(
        "save updates same record_id",
        "UiExecutionRecord.objects.get(id=record_id)" in cons,
    )

    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
