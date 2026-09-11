"""Read-only helpers for unified runtime log files under LOG_DIR."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from django.conf import settings


LEVEL_TOKENS = {
    "DEBUG": "[DEBUG]",
    "INFO": "[INFO]",
    "WARNING": "[WARNING]",
    "WARN": "[WARNING]",
    "ERROR": "[ERROR]",
    "CRITICAL": "[CRITICAL]",
}


def get_log_dir() -> Path:
    log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.DATA_DIR) / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir.resolve()


def resolve_log_file(filename: Optional[str] = None) -> Path:
    """Resolve a log filename inside LOG_DIR; reject path traversal."""
    log_dir = get_log_dir()
    name = (filename or "app.log").strip() or "app.log"
    safe_name = Path(name).name
    if safe_name != name or ".." in name:
        raise ValueError("非法日志文件名")
    if not (safe_name == "app.log" or safe_name.startswith("app.log.")):
        raise ValueError("仅允许查看 app.log 及其归档")

    target = (log_dir / safe_name).resolve()
    try:
        target.relative_to(log_dir)
    except ValueError as exc:
        raise ValueError("非法日志路径") from exc
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"日志文件不存在: {safe_name}")
    return target


def list_log_files() -> List[dict]:
    log_dir = get_log_dir()
    files = []
    for path in sorted(log_dir.glob("app.log*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return files


def _iter_tail_lines(path: Path, max_lines: int) -> List[str]:
    """Efficient-ish tail reader for moderate log files."""
    max_lines = max(1, max_lines)
    block_size = 8192
    data = b""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        position = fh.tell()
        while position > 0 and data.count(b"\n") <= max_lines:
            read_size = min(block_size, position)
            position -= read_size
            fh.seek(position)
            data = fh.read(read_size) + data
            if position == 0:
                break
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def read_runtime_logs(
    *,
    filename: Optional[str] = None,
    lines: int = 200,
    level: Optional[str] = None,
) -> dict:
    lines = min(max(int(lines or 200), 1), 2000)
    path = resolve_log_file(filename)
    raw_lines = _iter_tail_lines(path, lines * 5 if level else lines)

    level_key = (level or "").strip().upper()
    token = LEVEL_TOKENS.get(level_key)
    if token:
        filtered = [line for line in raw_lines if token in line]
        # Keep last N after filter
        selected = filtered[-lines:]
    else:
        selected = raw_lines[-lines:]

    stat = path.stat()
    return {
        "file": path.name,
        "path": str(path),
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "lines": selected,
        "line_count": len(selected),
        "level": level_key or None,
    }
