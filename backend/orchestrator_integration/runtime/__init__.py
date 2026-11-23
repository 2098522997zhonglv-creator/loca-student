"""任务运行时边界：策略 / 租约 / 存储 / 编排。

- 纯决策：``resume_policy``（无 Django，可单测）
- 副作用：``lease_service`` / ``task_store`` / ``create_or_resume``（需 Django）

外部仍可从 ``orchestrator_integration.agent_runtime`` 导入同名符号。
"""

from __future__ import annotations

from typing import Any

from orchestrator_integration.runtime.resume_policy import (
    ResumeAction,
    ResumeDecision,
    ResumePolicy,
)

__all__ = [
    "ResumeAction",
    "ResumeDecision",
    "ResumePolicy",
    "acquire_lease",
    "create_or_resume_task",
    "heartbeat_lease",
    "park_for_approval",
    "release_lease",
    "try_claim_execution",
]


def __getattr__(name: str) -> Any:
    # 延迟加载需 Django 的符号，避免 import 纯策略时被拖下水
    if name == "create_or_resume_task":
        from orchestrator_integration.runtime.create_or_resume import (
            create_or_resume_task as _fn,
        )

        return _fn
    if name in {
        "acquire_lease",
        "heartbeat_lease",
        "park_for_approval",
        "release_lease",
        "try_claim_execution",
    }:
        from orchestrator_integration.runtime import lease_service as _ls

        return getattr(_ls, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
