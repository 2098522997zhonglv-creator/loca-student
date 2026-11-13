"""任务语言与消息阶段协议（RC11 / FR10.5 / D10）。

response_locale 持久化到任务配置；SSE 用 message_kind 区分 progress/final/debug，
前端不得仅凭字母比例推断阶段。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_EN_NARRATION_RE = re.compile(
    r"\b(I('ll| will)|Let me|Now I|Calling|Using the|I'm going to|I am going to|"
    r"Please wait|I'll now|I will now)\b",
    re.I,
)


def infer_response_locale(
    user_message: str = "",
    *,
    explicit: str = "",
) -> str:
    """返回 zh | en | auto。显式覆盖优先；否则按用户消息 CJK 密度推断。"""
    ex = (explicit or "").strip().lower()
    if ex in {"zh", "zh-cn", "zh_hans", "chinese"}:
        return "zh"
    if ex in {"en", "en-us", "english"}:
        return "en"
    if ex in {"auto"}:
        return "auto"
    text = (user_message or "").strip()
    if not text:
        return "auto"
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cjk >= 2 and cjk >= latin * 0.15:
        return "zh"
    if latin >= 12 and cjk == 0:
        return "en"
    return "auto"


def looks_like_english_tool_narration(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return False
    cjk = len(_CJK_RE.findall(t))
    latin = len(_LATIN_RE.findall(t))
    if cjk >= 4:
        return False
    if latin < 12:
        return False
    if _EN_NARRATION_RE.search(t):
        return True
    return latin / max(len(t), 1) > 0.55


def tag_sse_event(
    event: Dict[str, Any],
    *,
    message_kind: str,
    response_locale: str = "",
) -> Dict[str, Any]:
    """给 SSE 事件打上阶段协议字段（不覆盖已有同名字段）。"""
    out = dict(event)
    out.setdefault("message_kind", message_kind)
    if response_locale:
        out.setdefault("response_locale", response_locale)
    return out


def locale_hint_for_prompt(locale: str) -> str:
    if locale == "zh":
        return (
            "# response_locale=zh\n"
            "- 进度与最终答复必须使用中文；禁止英文工具旁白固化为普通回复。\n"
            "- 代码、URL、标识符原文可保留，不要强行翻译。"
        )
    if locale == "en":
        return (
            "# response_locale=en\n"
            "- Keep progress and final answers in English; do not force Chinese.\n"
            "- Preserve code, URLs, and identifiers verbatim."
        )
    return "# response_locale=auto\n- Match the user's language for progress and final answers."


def should_demote_stream_chunk(*, locale: str, text: str) -> bool:
    """中文任务下，英文工具旁白应降级为 progress，不得进 final 气泡。"""
    if locale != "zh":
        return False
    return looks_like_english_tool_narration(text)
