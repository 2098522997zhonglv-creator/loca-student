"""轻量意图路由：按用户话术裁工具、注入专用提示，减少误调与空转。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

INTENT_QA = "qa"
INTENT_SEARCH = "search"
INTENT_WEB = "web"
INTENT_WRITE = "write"
INTENT_BROWSER = "browser"

_ALL_INTENTS = (
    INTENT_QA,
    INTENT_SEARCH,
    INTENT_WEB,
    INTENT_WRITE,
    INTENT_BROWSER,
)

_WRITE_HINTS = (
    "保存",
    "写入",
    "入库",
    "落库",
    "创建用例",
    "添加用例",
    "新建用例",
    "删除用例",
    "更新用例",
    "创建模块",
    "新建模块",
    "添加模块",
    "删掉",
    "删除模块",
    "add_testcase",
    "add_module",
    "写入平台",
    "同步到平台",
    "确认写入",
    "确认保存",
    "同意保存",
    "同意写入",
    "可以保存",
    "可以写入",
)

_WEB_HINTS = (
    "读网页",
    "读取网页",
    "打开链接",
    "抓取",
    "网页内容",
    "url-reader",
    "看一下这个链接",
    "打开这个网址",
)

_BROWSER_HINTS = (
    "浏览器",
    "自动化",
    "playwright",
    "点击按钮",
    "页面登录",
    "截图验证",
    "ui自动化",
    "UI自动化",
    "端到端",
    "e2e",
)

_SEARCH_HINTS = (
    "再搜",
    "搜索知识库",
    "知识库搜",
    "换个关键词",
    "knowledge_search",
    "检索一下",
)

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# MCP / 内置工具名匹配（小写比较）
_PLAYWRIGHT_TOOL_RE = re.compile(r"(?i)playwright|browser_")


@dataclass
class IntentDecision:
    intents: Set[str] = field(default_factory=set)
    primary: str = INTENT_QA
    reason: str = ""

    @property
    def needs_skills_execute(self) -> bool:
        return bool(
            self.intents
            & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER}
        )

    @property
    def needs_playwright(self) -> bool:
        return INTENT_BROWSER in self.intents

    @property
    def pure_kb_qa(self) -> bool:
        """纯知识库问答：无需副作用工具。"""
        return self.intents <= {INTENT_QA, INTENT_SEARCH} and bool(self.intents)


def classify_user_intent(message: str) -> IntentDecision:
    """基于关键词的多标签意图识别（无 LLM，低延迟）。"""
    text = (message or "").strip()
    if not text:
        return IntentDecision(intents={INTENT_QA}, primary=INTENT_QA, reason="empty")

    found: Set[str] = set()
    lower = text.lower()

    if any(h in text for h in _WRITE_HINTS) or any(
        h in lower for h in ("add_testcase", "add_module", "delete_")
    ):
        found.add(INTENT_WRITE)

    if any(h in text for h in _WEB_HINTS) or _URL_RE.search(text):
        found.add(INTENT_WEB)

    if any(h in text for h in _BROWSER_HINTS) or "playwright" in lower:
        found.add(INTENT_BROWSER)

    if any(h in text for h in _SEARCH_HINTS):
        found.add(INTENT_SEARCH)

    if not found:
        found.add(INTENT_QA)

    # 有副作用意图时仍保留 qa，便于「查完再写」
    if found & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER}:
        found.add(INTENT_QA)

    priority = (
        INTENT_WRITE,
        INTENT_BROWSER,
        INTENT_WEB,
        INTENT_SEARCH,
        INTENT_QA,
    )
    primary = next((p for p in priority if p in found), INTENT_QA)
    reason = ",".join(sorted(found))
    return IntentDecision(intents=found, primary=primary, reason=reason)


def _tool_name(tool: Any) -> str:
    return (
        getattr(tool, "name", None)
        or getattr(tool, "__name__", None)
        or str(tool)
    )


def filter_tools_by_intent(
    tools: Sequence[Any],
    decision: IntentDecision,
) -> List[Any]:
    """按意图裁剪工具列表；未知工具默认保留（安全偏保守）。"""
    if not tools:
        return []

    keep_execute = decision.needs_skills_execute
    keep_playwright = decision.needs_playwright
    # 纯问答也保留 read_skill_content，便于偶尔查 Skill 说明；去掉 execute 与浏览器
    out: List[Any] = []
    dropped: List[str] = []

    for tool in tools:
        name = _tool_name(tool)
        lname = (name or "").lower()

        if name == "execute_skill_script" and not keep_execute:
            dropped.append(name)
            continue

        if _PLAYWRIGHT_TOOL_RE.search(lname) and not keep_playwright:
            # 纯问答/检索时去掉浏览器 MCP，降低误点页面风险
            if decision.pure_kb_qa or (
                not keep_playwright and INTENT_BROWSER not in decision.intents
            ):
                dropped.append(name)
                continue

        out.append(tool)

    if dropped:
        logger.info(
            "意图路由裁剪工具 primary=%s intents=%s dropped=%s kept=%s",
            decision.primary,
            sorted(decision.intents),
            dropped,
            [_tool_name(t) for t in out],
        )
    return out


def build_intent_hint(decision: IntentDecision) -> str:
    """注入到 system prompt 的本轮意图补充。"""
    lines = [
        "# 本轮意图路由",
        f"- 识别意图: {', '.join(sorted(decision.intents)) or 'qa'}（主意图: {decision.primary}）",
    ]

    if decision.pure_kb_qa:
        lines.extend(
            [
                "- 本轮以知识库问答为主：优先用预检索/knowledge_search 回答，须标注来源。",
                "- 未挂载 execute_skill_script；不要编造落库或浏览器操作。",
                "- 若用户其实要保存/读网页，先确认意图再让用户重说「保存/打开网页」。",
            ]
        )
    if INTENT_WRITE in decision.intents:
        lines.extend(
            [
                "- 落库剧本（必须按序）：",
                "  1) read_skill_content(loca-stude)",
                "  2) 必要时只读 get_/list_ 查现有数据",
                "  3) 向用户展示将写入的字段摘要并征求确认",
                "  4) 用户明确同意后 execute_skill_script(..., user_confirmed=true, commands=[...], parallel=false)",
                "- 禁止未确认写入；多条写入用一条 commands 串行组合。",
            ]
        )
    if INTENT_WEB in decision.intents:
        lines.extend(
            [
                "- 读网页：优先只走 url-reader：read_skill_content(url-reader) → "
                "execute_skill_script(skill_name=url-reader, command=python scripts/read_url.py \"URL\")。",
                "- 禁止把 url-reader / playwright-skill / browser-use 当 tool 名直接调用。",
                "- 内网 URL（192.168.*/10.*/127.0.0.1）必须先调用 url-reader，不要未调用就宣称「无法访问局域网」。",
                "- url-reader 失败时：把错误原文告知用户；同一 URL 不要再连环试 playwright/browser-use/"
                "换参重试超过 1 次。仅当错误明确是「需 JS 渲染 / 登录态」且用户同意时，才改用 playwright-skill。",
                "- 禁止对 browser-use 使用无文档的裸命令（如 goto URL）。",
            ]
        )
    if INTENT_BROWSER in decision.intents:
        lines.append(
            "- 浏览器自动化：read_skill_content(playwright-skill) → execute_skill_script，多步共用 session_id。"
        )
    if INTENT_SEARCH in decision.intents and INTENT_WRITE not in decision.intents:
        lines.append("- 用户要求再检索：可调用 knowledge_search，避免重复相同 query。")

    lines.append(
        "- 事实类结论必须带来源（预检索编号或文档名）；多来源冲突时并列说明，勿擅自二选一。"
    )
    return "\n".join(lines)


def apply_intent_routing(
    message: str,
    tools: Optional[Sequence[Any]] = None,
) -> tuple[IntentDecision, List[Any], str]:
    """一站式：分类 → 裁工具 → 提示词。"""
    decision = classify_user_intent(message)
    filtered = filter_tools_by_intent(list(tools or []), decision)
    hint = build_intent_hint(decision)
    return decision, filtered, hint
