"""功能开关：TASK_ORCHESTRATOR_ENABLED。关闭后新任务回退旧 zone/commission 路径。"""

from __future__ import annotations

import os
from typing import Any, Optional

STRATEGY_VERSION = "task_orchestrator_v1"


def is_task_orchestrator_enabled(
    *,
    overrides: Optional[dict] = None,
    prior_facts: Optional[dict] = None,
) -> bool:
    """是否走新编排路径。

    - 环境变量 / Django settings ``TASK_ORCHESTRATOR_ENABLED``（默认 true）
    - overrides 可强制开关（配置 API）
    - 在途任务若已记录 strategy_version，沿用该策略，避免中途切换重放写操作
    """
    if isinstance(prior_facts, dict):
        ver = (prior_facts.get("strategy_version") or "").strip()
        if ver == STRATEGY_VERSION:
            return True
        if ver and ver != STRATEGY_VERSION:
            # 旧策略在途任务不切到新路径
            if ver.startswith("legacy") or ver == "zone_commission_v1":
                return False

    if isinstance(overrides, dict) and "task_orchestrator_enabled" in overrides:
        raw = overrides.get("task_orchestrator_enabled")
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    # 显式环境变量优先于已加载的 Django settings（便于测试与运维热开关）
    env_raw = os.environ.get("TASK_ORCHESTRATOR_ENABLED")
    if env_raw is not None and str(env_raw).strip() != "":
        return str(env_raw).strip().lower() in ("1", "true", "yes", "on")

    try:
        from django.conf import settings

        if hasattr(settings, "TASK_ORCHESTRATOR_ENABLED"):
            return bool(settings.TASK_ORCHESTRATOR_ENABLED)
    except Exception:
        pass

    return True


def strategy_version_for_new_task(enabled: bool) -> str:
    return STRATEGY_VERSION if enabled else "zone_commission_v1"


def merge_strategy_into_facts(
    facts: Optional[dict], *, enabled: bool
) -> dict[str, Any]:
    out = dict(facts or {})
    if "strategy_version" not in out:
        out["strategy_version"] = strategy_version_for_new_task(enabled)
    return out
