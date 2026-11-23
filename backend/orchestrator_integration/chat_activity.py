"""聊天活跃心跳：供 Windows 自动更新在杀进程前等待空闲，避免打断进行中的 SSE。"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MARKER_NAME = "chat_active.ts"
_GIT_HEAD_CACHE: Optional[str] = None


def _marker_path() -> Optional[Path]:
    try:
        from django.conf import settings

        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.DATA_DIR) / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / _MARKER_NAME
    except Exception:
        return None


def normalize_user_message(message: Any) -> Any:
    """压平前端偶发的 tab/\\t/\\r 等空白；非 str 原样返回。

    独立函数便于单测，也避免在 View.post 内再 import re 触发 UnboundLocalError。
    """
    if not isinstance(message, str):
        return message
    # 既处理真实控制字符，也处理字面量 "\t"/"\n"（日志里常见 \\t）
    text = message.replace("\\t", " ").replace("\\n", " ").replace("\\r", " ")
    return re.sub(r"[\t\r\f\v]+", " ", text).strip()


def runtime_git_head() -> str:
    """短 git sha，便于日志确认机器跑的是哪一版（有缓存）。"""
    global _GIT_HEAD_CACHE
    if _GIT_HEAD_CACHE:
        return _GIT_HEAD_CACHE
    try:
        from django.conf import settings

        root = Path(settings.BASE_DIR).resolve().parent
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        _GIT_HEAD_CACHE = out.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:
        _GIT_HEAD_CACHE = "unknown"
    return _GIT_HEAD_CACHE


def touch_chat_active(*, session_id: str = "", reason: str = "") -> None:
    """写入当前时间戳；失败静默（不影响对话）。"""
    path = _marker_path()
    if path is None:
        return
    try:
        now = time.time()
        path.write_text(
            f"{now:.3f}\tsession={session_id or '-'}\treason={reason or '-'}\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("touch_chat_active failed: %s", exc)


def seconds_since_chat_active() -> Optional[float]:
    path = _marker_path()
    if path is None or not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip().split()[0]
        return max(0.0, time.time() - float(raw))
    except Exception:
        return None


def import_agent_loop_routing_deps() -> dict:
    """重依赖导入放到线程里跑，避免堵死 Daphne 事件循环导致 SSE 假死。"""
    from orchestrator_integration.builtin_tools import get_builtin_tools
    from orchestrator_integration.builtin_tools.skill_tools import (
        detect_user_write_approval,
    )
    from orchestrator_integration.intent_router import (
        apply_intent_routing,
        classify_user_intent,
        decide_kb_access_policy,
    )
    from orchestrator_integration.skill_zones import (
        ZONE_BROWSER,
        resolve_skill_zone,
        zone_skills,
    )
    from orchestrator_integration.turn_context import (
        TURN_AMBIGUOUS,
        ambiguous_candidates_payload,
        classify_turn_relation,
        executable_open_tasks,
        list_open_task_contexts,
        load_open_task_context,
        routing_text_for_classify,
        should_preserve_execution,
        stage_specs_from_prior,
    )

    return {
        "get_builtin_tools": get_builtin_tools,
        "detect_user_write_approval": detect_user_write_approval,
        "apply_intent_routing": apply_intent_routing,
        "classify_user_intent": classify_user_intent,
        "decide_kb_access_policy": decide_kb_access_policy,
        "ZONE_BROWSER": ZONE_BROWSER,
        "resolve_skill_zone": resolve_skill_zone,
        "zone_skills": zone_skills,
        "TURN_AMBIGUOUS": TURN_AMBIGUOUS,
        "ambiguous_candidates_payload": ambiguous_candidates_payload,
        "classify_turn_relation": classify_turn_relation,
        "executable_open_tasks": executable_open_tasks,
        "list_open_task_contexts": list_open_task_contexts,
        "load_open_task_context": load_open_task_context,
        "routing_text_for_classify": routing_text_for_classify,
        "should_preserve_execution": should_preserve_execution,
        "stage_specs_from_prior": stage_specs_from_prior,
    }
