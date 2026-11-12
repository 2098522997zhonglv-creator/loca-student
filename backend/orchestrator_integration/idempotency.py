"""幂等键生成与下发（FR06.4）。

本地 operation_id ≠ 外部恰好一次保障；对支持的 Skill 将键注入环境变量，
由脚本/API 自行传递。不支持时仍记录键供核对关联。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from orchestrator_integration.skill_catalog import load_skill_catalog


def build_idempotency_key(
    *,
    task_id: str = "",
    skill_name: str = "",
    command: str = "",
    action: str = "",
    resource_key: str = "",
) -> str:
    raw = json.dumps(
        {
            "task_id": task_id or "",
            "skill": skill_name or "",
            "action": action or "",
            "command": (command or "")[:800],
            "resource": resource_key or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def should_inject_idempotency(skill_name: str, command: str = "") -> bool:
    """写/执行类动作才注入；只读跳过。"""
    catalog = load_skill_catalog()
    entry = catalog.get(skill_name or "")
    if entry is None:
        # 未知能力：保守注入（调用方可忽略）
        return True
    effect = entry.side_effect or "unknown"
    if effect in {"read"}:
        return False
    # 命令像只读 action 时跳过
    blob = (command or "").lower()
    if any(
        x in blob
        for x in (
            "--action get_",
            "--action list_",
            "--action search",
            "--action preview",
            "get_execution_records",
        )
    ):
        return False
    return True


def idempotency_env(
    *,
    task_id: str = "",
    skill_name: str = "",
    command: str = "",
    step_id: str = "",
) -> Dict[str, str]:
    if not should_inject_idempotency(skill_name, command):
        return {}
    key = build_idempotency_key(
        task_id=task_id,
        skill_name=skill_name,
        command=command,
        resource_key=step_id,
    )
    return {
        "LOCA_IDEMPOTENCY_KEY": key,
        "IDEMPOTENCY_KEY": key,
        "X_IDEMPOTENCY_KEY": key,
        "LOCA_IDEMPOTENCY_NOTE": (
            "local_key_not_exactly_once; pass to APIs that support it; "
            "otherwise use for reconcile correlation only"
        ),
    }


def annotate_result_with_key(text: str, key: str) -> str:
    if not key or not text:
        return text
    if "idempotency_key" in text:
        return text
    return text + f"\n\n[idempotency_key]={key} (local correlation; not external EOS guarantee)"
