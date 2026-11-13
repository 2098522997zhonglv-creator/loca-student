#!/usr/bin/env python3
"""生成文档 13.1/14 要求的验收证据包骨架。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "acceptance_evidence"
PYTHON = os.environ.get(
    "LOCA_TEST_PYTHON", "/opt/miniconda3/envs/loca_test/bin/python"
)


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT)
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _run_rc_subset() -> dict:
    tests = [
        "orchestrator_integration.test_rc07_approval_persist",
        "orchestrator_integration.test_rc05_rc06_rc09_rc12",
        "orchestrator_integration.test_rc08_rc10_rc16",
        "orchestrator_integration.test_rc17",
        "orchestrator_integration.test_rc20_rc21_rc22",
        "orchestrator_integration.test_rc03_rc14_rc15",
        "orchestrator_integration.test_rc23_deploy_metrics",
        "orchestrator_integration.test_budget_reserve_rc08_rc09",
        "orchestrator_integration.test_d05_output_refs",
        "orchestrator_integration.test_d01_approval_pending",
        "orchestrator_integration.test_multiturn_route",
    ]
    cmd = [PYTHON, "manage.py", "test", *tests, "-v1"]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "loca_stude_django.settings")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT / "backend"),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout or "")[-4000:] + "\n" + (proc.stderr or "")[-2000:]
        return {
            "ok": ok,
            "returncode": proc.returncode,
            "tests": tests,
            "tail": tail,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"python not found: {PYTHON}", "tests": tests}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "tests": tests}


def _callie_skip() -> dict:
    required = [
        "CALLIE_BASE_URL",
        "CALLIE_USERNAME",
        "CALLIE_PASSWORD",
        "CALLIE_PRODUCT_URL",
    ]
    missing = [k for k in required if not (os.environ.get(k) or "").strip()]
    return {
        "rc": "RC18",
        "status": "skipped" if missing else "env_ready",
        "missing_env": missing,
        "note": "缺环境不计通过；设置全部 CALLIE_* 后重跑 callie_e2e",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logs").mkdir(exist_ok=True)
    sha = _git_sha()
    print(f"git_sha={sha}")
    print("running RC subset…")
    suite = _run_rc_subset()
    callie = _callie_skip()

    # RC 状态索引（本地已有测试 → local_pass/fail；Callie → skip）
    rc_results = {
        "RC01": {"status": "local_covered", "via": "test_multiturn_route"},
        "RC02": {"status": "local_covered", "via": "test_d02_composite_kb_mount"},
        "RC03": {"status": "local_covered", "via": "test_rc03_rc14_rc15"},
        "RC04": {"status": "local_covered", "via": "test_d05_output_refs"},
        "RC05": {"status": "local_covered", "via": "test_rc05_rc06_rc09_rc12"},
        "RC06": {"status": "local_covered", "via": "test_rc05_rc06_rc09_rc12"},
        "RC07": {"status": "local_covered", "via": "test_rc07_approval_persist"},
        "RC08": {"status": "local_covered", "via": "test_budget_reserve_rc08_rc09"},
        "RC09": {"status": "local_covered", "via": "test_budget_reserve_rc08_rc09"},
        "RC10": {"status": "local_covered", "via": "test_rc08_rc10_rc16"},
        "RC11": {"status": "local_covered", "via": "test_rc11_message_locale"},
        "RC12": {"status": "local_covered", "via": "test_rc05_rc06_rc09_rc12"},
        "RC13": {"status": "local_covered", "via": "streamUiHelpers.test.ts"},
        "RC14": {"status": "local_covered", "via": "test_rc03_rc14_rc15"},
        "RC15": {"status": "local_covered", "via": "test_rc03_rc14_rc15"},
        "RC16": {"status": "local_covered", "via": "test_rc08_rc10_rc16"},
        "RC17": {"status": "local_covered", "via": "test_rc17"},
        "RC18": callie,
        "RC19": {"status": "local_covered", "via": "test_rc08_rc10_rc16"},
        "RC20": {"status": "local_covered", "via": "test_rc20_rc21_rc22"},
        "RC21": {"status": "local_covered", "via": "test_rc20_rc21_rc22"},
        "RC22": {"status": "local_covered", "via": "test_rc20_rc21_rc22"},
        "RC23": {"status": "local_covered", "via": "test_rc23_deploy_metrics"},
        "RC24": {
            "status": "partial",
            "note": "需干净锁文件安装 + vue-tsc/组件门禁；本脚本未代替完整前端门禁",
        },
    }

    try:
        sys.path.insert(0, str(ROOT / "backend"))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "loca_stude_django.settings")
        import django

        django.setup()
        from orchestrator_integration.agent_runtime import runtime_deploy_info

        deploy = runtime_deploy_info()
    except Exception as exc:
        deploy = {"error": str(exc), "git_sha": sha}

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "deploy": deploy,
        "suite": {
            "ok": suite.get("ok"),
            "returncode": suite.get("returncode"),
            "error": suite.get("error"),
        },
        "full_acceptance": bool(suite.get("ok")) and callie.get("status") == "env_ready",
        "note": (
            "full_acceptance=true 仅当本地套件绿且 Callie 环境齐全；"
            "否则不得宣称全面验收通过"
        ),
    }

    (OUT / "snapshot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "rc_results.json").write_text(
        json.dumps(rc_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "callie_skip_reasons.json").write_text(
        json.dumps(callie, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "logs" / "rc_subset_tail.txt").write_text(
        suite.get("tail") or suite.get("error") or "", encoding="utf-8"
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if suite.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
