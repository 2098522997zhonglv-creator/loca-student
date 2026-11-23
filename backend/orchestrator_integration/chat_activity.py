"""聊天活跃心跳：供 Windows 自动更新在杀进程前等待空闲，避免打断进行中的 SSE。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MARKER_NAME = "chat_active.ts"


def _marker_path() -> Optional[Path]:
    try:
        from django.conf import settings

        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.DATA_DIR) / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / _MARKER_NAME
    except Exception:
        return None


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
