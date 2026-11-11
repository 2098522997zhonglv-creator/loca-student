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
INTENT_PLATFORM = "platform"
INTENT_CHITCHAT = "chitchat"
INTENT_META = "meta"

_ALL_INTENTS = (
    INTENT_QA,
    INTENT_SEARCH,
    INTENT_WEB,
    INTENT_WRITE,
    INTENT_BROWSER,
    INTENT_PLATFORM,
)

# 平台数据只读查询：动词 + 平台实体同时出现
_PLATFORM_READ_VERB_RE = re.compile(
    r"查看|查询|查一下|查下|查查|看看|看下|看一下|列出|列一下|有哪些|已有|现有|多少|统计|详情|打开"
)
_PLATFORM_ENTITY_RE = re.compile(
    r"用例|模块|项目|需求|套件|执行记录|执行历史|测试结果|评审|缺陷|页面步骤|元素|ui自动化|UI自动化|截图|附件|文件"
)
# 跑用例 / 执行自动化：必须挂上 execute_skill_script（开着知识库也不能裁掉）
_PLATFORM_RUN_RE = re.compile(
    r"(执行|运行|跑一?下|跑一下|跑下|启动|确认执行).{0,16}(用例|测试用例|套件|自动化|场景)"
    r"|(用例|测试用例|套件|自动化).{0,8}(执行|运行|跑一?下|跑一下|跑下)"
    r"|执行(?:ID为|id\s*=?\s*|编号)?\s*\d+.{0,10}(?:测试)?用例"
    r"|执行用例\s*[#＃]?\s*\d+"
    r"|跑(?:一?下)?用例\s*[#＃]?\s*\d+"
    r"|帮我(?:执行|跑|运行).{0,10}用例"
    r"|execute_testcase|run_testcase|batch_run"
)
_EXECUTE_APPROVAL_RE = re.compile(
    r"(确认|同意|允许|可以|直接)(执行|运行|跑)"
    r"|(执行|运行)吧"
    r"|开始执行"
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
    # 工单/知识库沉淀路径（workorder-knowledge）
    "知识沉淀",
    "沉淀到知识库",
    "写入知识库",
    "同步到知识库",
    "同步知识库",
    "入库知识库",
    "upsert_knowledge",
    "workorder-knowledge",
    "工单采集",
    "steps_file",
)

# Teambition / 钉钉工单：SPA，禁止当静态网页走 url-reader
_TEAMBITION_TASK_RE = re.compile(
    r"teambition\.com/(?:task|project|sprint)/", re.IGNORECASE
)
_TICKET_VIEW_RE = re.compile(
    r"(查看|打开|读|看|打开下|看下|查看下).{0,8}工单"
    r"|这个工单|该工单|工单链接|钉钉工单|teambition\s*工单",
    re.IGNORECASE,
)

# 明确要求跑 Skill 脚本时也要挂上 execute（否则只剩 read_skill_content）
_SKILL_EXECUTE_HINTS = (
    "execute_skill_script",
    "执行 skill",
    "执行Skill",
    "执行脚本",
    "跑 skill",
    "跑Skill",
    "按 skill 执行",
    "按SKILL执行",
    "按文档执行",
    "调用 skill",
    "调用Skill",
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
    "执行自动化",
    "跑自动化",
)

_SEARCH_HINTS = (
    "再搜",
    "搜索知识库",
    "知识库搜",
    "换个关键词",
    "knowledge_search",
    "检索一下",
)

# 强制再查知识库（含纠错后要不同答案）
_FORCE_KB_HINTS = (
    "再查一下",
    "再查下",
    "重新查",
    "重新检索",
    "查知识库",
    "问知识库",
    "搜知识库",
    "换个答案",
    "换一个答案",
    "不是这个",
    "不是这个答案",
    "要最新的",
    "最新版本",
    "更新了吗",
    "有没有更新",
    "以知识库为准",
)

_CHITCHAT_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hi|hello|hey|在吗|在不在|谢谢|感谢|多谢|好的|嗯|哦|ok|okay|收到|明白了|知道了|没事了)[\s!！。.~～？?]*$",
    re.IGNORECASE,
)

_META_HINTS = (
    "为什么你",
    "你刚才",
    "你之前",
    "你怎么没",
    "你为什么",
    "刚才为什么",
    "再说详细",
    "展开说说",
    "换个说法",
    "用更简单",
    "总结一下刚才",
    "你说的意思",
)

_CONTINUE_HINTS = ("继续", "接着", "往下", "再写", "补充一下", "接着写", "继续生")

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
            & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER, INTENT_PLATFORM}
        )

    @property
    def needs_playwright(self) -> bool:
        return INTENT_BROWSER in self.intents

    @property
    def pure_kb_qa(self) -> bool:
        """纯知识库问答：无需副作用工具。"""
        return self.intents <= {INTENT_QA, INTENT_SEARCH} and bool(self.intents)

    @property
    def is_chitchat_or_meta(self) -> bool:
        return bool(self.intents & {INTENT_CHITCHAT, INTENT_META})


@dataclass
class KbAccessPolicy:
    """本轮是否预检索/强制查库/允许 knowledge_search。"""

    skip_prefetch: bool = False
    force_kb: bool = False
    allow_knowledge_search: bool = True
    reason: str = ""
    try_answer_cache: bool = True


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

    # 对上一轮「是否确认写入？」的同意回复，必须保留执行工具
    from orchestrator_integration.builtin_tools.skill_tools import (
        detect_user_write_approval,
    )

    if detect_user_write_approval(text):
        found.add(INTENT_WRITE)

    if any(h in text for h in _SKILL_EXECUTE_HINTS) or "execute_skill" in lower:
        # 用户点名要跑脚本：至少挂上执行工具（写入仍走确认门）
        found.add(INTENT_PLATFORM)

    if _PLATFORM_READ_VERB_RE.search(text) and _PLATFORM_ENTITY_RE.search(text):
        found.add(INTENT_PLATFORM)

    if _PLATFORM_RUN_RE.search(text):
        found.add(INTENT_PLATFORM)
        # 含 UI/浏览器语义时同时标 browser，便于挂 playwright 类工具
        if any(h in text for h in ("自动化", "浏览器", "playwright", "ui", "UI", "e2e")):
            found.add(INTENT_BROWSER)

    if any(h in text for h in _WEB_HINTS) or _URL_RE.search(text):
        found.add(INTENT_WEB)

    # 工单链接是登录态 SPA：标 platform，并去掉误判的 web（避免 url-reader）
    if is_ticket_workorder_intent(text):
        found.add(INTENT_PLATFORM)
        found.discard(INTENT_WEB)
        # 采集页常需浏览器；仅「沉淀/写入」时不强制 browser
        if _TEAMBITION_TASK_RE.search(text) or _TICKET_VIEW_RE.search(text):
            if not any(
                h in text
                for h in ("沉淀到知识库", "写入知识库", "知识沉淀", "upsert_knowledge")
            ):
                found.add(INTENT_BROWSER)

    if any(h in text for h in _BROWSER_HINTS) or "playwright" in lower:
        found.add(INTENT_BROWSER)

    if any(h in text for h in _SEARCH_HINTS) or any(
        h in text for h in _FORCE_KB_HINTS
    ):
        found.add(INTENT_SEARCH)

    # 寒暄 / 对上一轮回答的元问题（默认不查库）
    if _CHITCHAT_RE.match(text) and not (
        found & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER, INTENT_PLATFORM, INTENT_SEARCH}
    ):
        found.add(INTENT_CHITCHAT)
    elif any(h in text for h in _META_HINTS) and not (
        found & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER, INTENT_PLATFORM, INTENT_SEARCH}
    ):
        found.add(INTENT_META)

    if not found:
        found.add(INTENT_QA)

    # 「先读网页，总结后再问是否保存」——本轮主路径是读，不要当成立即落库
    if INTENT_WEB in found and INTENT_WRITE in found:
        if re.search(r"是否保存|问我是否|总结后.*问|先总结|问我要不要", text):
            found.discard(INTENT_WRITE)

    # 有副作用意图时仍保留 qa，便于「查完再写」
    if found & {INTENT_WRITE, INTENT_WEB, INTENT_BROWSER, INTENT_PLATFORM}:
        found.add(INTENT_QA)

    # 同时有 web+write 时，优先 web（先读再写）
    if INTENT_WEB in found and INTENT_WRITE in found:
        priority = (
            INTENT_WEB,
            INTENT_WRITE,
            INTENT_BROWSER,
            INTENT_PLATFORM,
            INTENT_SEARCH,
            INTENT_QA,
            INTENT_META,
            INTENT_CHITCHAT,
        )
    else:
        priority = (
            INTENT_WRITE,
            INTENT_BROWSER,
            INTENT_WEB,
            INTENT_PLATFORM,
            INTENT_SEARCH,
            INTENT_QA,
            INTENT_META,
            INTENT_CHITCHAT,
        )
    primary = next((p for p in priority if p in found), INTENT_QA)
    reason = ",".join(sorted(found))
    return IntentDecision(intents=found, primary=primary, reason=reason)


def is_ticket_workorder_intent(message: str) -> bool:
    """Teambition/钉钉工单查看或沉淀（非静态网页）。"""
    text = (message or "").strip()
    if not text:
        return False
    if _TEAMBITION_TASK_RE.search(text):
        return True
    if _TICKET_VIEW_RE.search(text):
        return True
    if any(
        h in text
        for h in (
            "钉钉工单",
            "工单采集",
            "工单需求",
            "workorder-knowledge",
            "知识沉淀",
            "沉淀到知识库",
            "upsert_knowledge",
        )
    ):
        return True
    return False


def looks_like_testcase_execute_keywords(message: str) -> bool:
    """仅关键字判定（不调用 classify，可供写入确认门安全使用）。"""
    text = (message or "").strip()
    if not text:
        return False
    if _PLATFORM_RUN_RE.search(text):
        return True
    if _EXECUTE_APPROVAL_RE.search(text) and (
        "用例" in text or "自动化" in text or "套件" in text or "testcase" in text.lower()
    ):
        return True
    return False


def is_testcase_execute_intent(
    message: str, decision: Optional[IntentDecision] = None
) -> bool:
    """用户明确要跑/确认执行平台用例或自动化（与知识库问答解耦）。"""
    text = (message or "").strip()
    if not text:
        return False
    if looks_like_testcase_execute_keywords(text):
        return True
    decision = decision or classify_user_intent(text)
    # 短确认「确认执行」且本轮已标 platform/browser（由上轮上下文话术带入时仍靠关键字）
    if _EXECUTE_APPROVAL_RE.search(text) and decision.intents & {
        INTENT_PLATFORM,
        INTENT_BROWSER,
    }:
        return True
    return False


def decide_kb_access_policy(
    message: str,
    decision: Optional[IntentDecision] = None,
    *,
    prefetch_query: str = "",
) -> KbAccessPolicy:
    """决定本轮是否跳过预检索 / 强制查库。"""
    text = (message or "").strip()
    decision = decision or classify_user_intent(text)
    pq = (prefetch_query or "").strip()

    # 确认/执行用例：完全离开知识库路径（无预检索、无精排、禁 knowledge_search）
    if is_testcase_execute_intent(text, decision):
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="testcase_execute",
            try_answer_cache=False,
        )

    # 查看/采集工单：先读工单本身，不要用工单 URL 去知识库瞎检索
    if is_ticket_workorder_intent(text) and not any(
        h in text for h in _FORCE_KB_HINTS
    ):
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="ticket_workorder",
            try_answer_cache=False,
        )

    from orchestrator_integration.behavior_memory import looks_like_user_correction

    force = False
    force_reason = ""
    if looks_like_user_correction(text):
        force = True
        force_reason = "user_correction"
    elif INTENT_SEARCH in decision.intents or any(h in text for h in _FORCE_KB_HINTS):
        force = True
        force_reason = "force_or_search"
    elif any(h in text for h in _SEARCH_HINTS):
        force = True
        force_reason = "search_hint"

    if force:
        return KbAccessPolicy(
            skip_prefetch=False,
            force_kb=True,
            allow_knowledge_search=True,
            reason=force_reason,
            try_answer_cache=False,
        )

    # 短续写
    if len(text) <= 16 and any(k in text for k in _CONTINUE_HINTS):
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="continue",
            try_answer_cache=False,
        )

    if INTENT_CHITCHAT in decision.intents or _CHITCHAT_RE.match(text):
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="chitchat",
            try_answer_cache=False,
        )

    if INTENT_META in decision.intents:
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="meta",
            try_answer_cache=False,
        )

    # 纯读网页且去掉 URL 后几乎无检索词
    if decision.primary == INTENT_WEB and len(pq) < 4:
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="web_only",
            try_answer_cache=False,
        )

    # 写入确认短句不必查库
    from orchestrator_integration.builtin_tools.skill_tools import (
        detect_user_write_approval,
    )

    if detect_user_write_approval(text) and len(text) <= 20:
        return KbAccessPolicy(
            skip_prefetch=True,
            force_kb=False,
            allow_knowledge_search=False,
            reason="write_approval",
            try_answer_cache=False,
        )

    return KbAccessPolicy(
        skip_prefetch=False,
        force_kb=False,
        allow_knowledge_search=True,
        reason="normal",
        try_answer_cache=True,
    )


def _tool_name(tool: Any) -> str:
    return (
        getattr(tool, "name", None)
        or getattr(tool, "__name__", None)
        or str(tool)
    )


def filter_tools_by_intent(
    tools: Sequence[Any],
    decision: IntentDecision,
    kb_active: bool = True,
    kb_policy: Optional[KbAccessPolicy] = None,
    skill_zone: Any = None,
) -> List[Any]:
    """按意图裁剪工具列表；未知工具默认保留（安全偏保守）。

    未挂知识库时纯问答无处可答，不裁 execute_skill_script（写入另有服务端确认门）。
    skill_zone.allow_execute=True 时强制保留 execute（分区已挂载白名单）。
    """
    if not tools:
        return []

    keep_execute = decision.needs_skills_execute or not kb_active
    if skill_zone is not None:
        if getattr(skill_zone, "allow_execute", False):
            # 分区明确允许执行（沉淀/跑用例/平台等）→ 即使 primary=qa 也保留
            keep_execute = True
        elif kb_active and not decision.needs_skills_execute:
            # 挂了知识库且分区禁止执行、意图也非副作用 → 裁掉 execute
            keep_execute = False
    keep_playwright = decision.needs_playwright
    drop_knowledge_search = bool(
        kb_policy
        and not kb_policy.allow_knowledge_search
        and not kb_policy.force_kb
    )
    # 纯问答也保留 read_skill_content，便于偶尔查 Skill 说明；去掉 execute 与浏览器
    out: List[Any] = []
    dropped: List[str] = []

    for tool in tools:
        name = _tool_name(tool)
        lname = (name or "").lower()

        if name == "execute_skill_script" and not keep_execute:
            dropped.append(name)
            continue

        if name == "knowledge_search" and drop_knowledge_search:
            dropped.append(name)
            continue

        if _PLAYWRIGHT_TOOL_RE.search(lname) and not keep_playwright:
            # 纯问答/检索时去掉浏览器 MCP，降低误点页面风险
            if decision.pure_kb_qa or decision.is_chitchat_or_meta or (
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


def build_intent_hint(
    decision: IntentDecision,
    kb_active: bool = True,
    kb_policy: Optional[KbAccessPolicy] = None,
) -> str:
    """注入到 system prompt 的本轮意图补充。"""
    lines = [
        "# 本轮意图路由",
        f"- 识别意图: {', '.join(sorted(decision.intents)) or 'qa'}（主意图: {decision.primary}）",
    ]
    if kb_policy and kb_active:
        lines.append(
            f"- 知识库策略: reason={kb_policy.reason} "
            f"skip_prefetch={kb_policy.skip_prefetch} force_kb={kb_policy.force_kb}"
        )

    if kb_policy and kb_policy.reason == "testcase_execute":
        lines.extend(
            [
                "- 本轮为「执行测试用例/自动化」：已跳过知识库预检索与精排；禁止调用 knowledge_search。",
                "- 同一会话内：若历史已出现过对应 Skill 的 read_skill_content，本轮不要再读；直接 execute_skill_script。",
                "- 首次需要说明时最多 read_skill_content 一次（ui-automation 或 loca-stude），随后只 execute。",
                "- 执行命令优先一条搞定："
                "python ui_automation_tools.py --action execute_testcase --testcase_id <N> --wait_result"
                "（或平台等价 execute/run action）。execute/run 是只读执行，无需 user_confirmed。",
                "- 成功后不要再紧跟 get_execution_records；仅当返回失败/timeout 且需要排查时，才查一次记录。",
                "- 未明确要求「生成/创建 UI 自动化」时：禁止 create_ui_* / set_case_steps / add_testcase；只跑执行。",
                "- 若用户同时要求生成 UI：先 execute_testcase 出结果；生成类写入须另问确认后再调（服务端会拦未确认写入）。",
                "- 多条互相独立的只读/执行：用 commands + parallel=true；凡含写入或有先后依赖：parallel=false 串行。",
                "- 同一命令失败后无差别重试不得超过 1 次；第二次仍失败则停止并原文汇报错误，不要换参连环试。",
                "- 不要为「显得在干活」去 knowledge_search、重复 read_skill_content 或重复拉执行记录。",
            ]
        )
    elif kb_policy and kb_policy.reason in ("chitchat", "meta", "continue") and kb_active:
        lines.extend(
            [
                "- 本轮无需查知识库：直接基于对话历史简短回复，不要调用 knowledge_search。",
            ]
        )
    elif kb_policy and kb_policy.force_kb and kb_active:
        lines.extend(
            [
                "- 用户要求更新/纠正答案：必须以本轮知识库预检索/knowledge_search 为准，"
                "不要沿用历史缓存结论。",
            ]
        )
    elif decision.pure_kb_qa and kb_active:
        lines.extend(
            [
                "- 本轮以知识库问答为主：优先用预检索或历史已答缓存；不足时再 knowledge_search，须标注来源。",
                "- 未挂载 execute_skill_script；不要编造落库、执行 Skill 或浏览器操作。",
                "- 若用户其实要保存/知识沉淀/执行 Skill/读网页：不要硬调不存在的工具；"
                "请明确请用户重说「保存到平台」「沉淀到知识库」或「执行 skill」。",
            ]
        )
    if INTENT_WRITE in decision.intents and (
        not kb_policy or kb_policy.reason != "testcase_execute"
    ):
        lines.extend(
            [
                "- 落库剧本（必须按序）：",
                "  1) read_skill_content(loca-stude)",
                "  2) 必要时只读 get_/list_ 查现有数据",
                "  3) 向用户展示将写入的字段摘要，以「是否确认写入？」结束本轮回复",
                "  4) 用户下一条消息回复「确认」后，再 execute_skill_script(..., user_confirmed=true, commands=[...], parallel=false)",
                "- 是否放行由服务端按用户原话判定，本轮用户没说同意时任何写入都会被拒，不要自行重试。",
                "- 多条写入用一条 commands 串行组合。",
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
                "- 禁止对 browser-use 使用裸命令（goto/new_tab/heredoc）；该类调用会被拦截并要求改用 url-reader。",
                "- 禁止用 playwright 仅 page.goto 打开 HTML 报告；会被拦截并要求改用 url-reader。",
            ]
        )
        if kb_active:
            lines.append(
                "- 用户问某个地址/域名「是什么、哪个环境、做什么用」时，先看知识库预检索里有没有这个域名："
                "有就以知识库说明为准并标注文档名，网页内容只作补充；预检索没有再 knowledge_search 搜域名，"
                "仍没有才明确说「知识库未收录」并读网页。"
            )
    if (
        INTENT_PLATFORM in decision.intents
        and INTENT_WRITE not in decision.intents
        and (not kb_policy or kb_policy.reason != "testcase_execute")
    ):
        lines.append(
            "- 查询平台数据：read_skill_content(loca-stude) → execute_skill_script 执行 get_/list_ 只读命令，"
            "无需确认；查到后直接汇总给用户，不要顺手写入。"
        )
        lines.append(
            "- 必须本轮实际调用工具拿到真实数据再回答；不要凭对话历史推测，"
            "也不要沿用历史里「工具不可用/调用失败」的结论（问题可能已修复）。"
            "若用户所说「已生成的用例」可能指本对话草稿，需同时说明：草稿（未保存）与平台已保存用例（查询结果）。"
        )
    if INTENT_BROWSER in decision.intents and (
        not kb_policy or kb_policy.reason != "testcase_execute"
    ):
        lines.append(
            "- 浏览器自动化：read_skill_content(playwright-skill) → execute_skill_script，多步共用 session_id。"
        )
    if INTENT_SEARCH in decision.intents and INTENT_WRITE not in decision.intents:
        lines.append("- 用户要求再检索：可调用 knowledge_search，避免重复相同 query。")

    lines.append(
        "- 事实类结论必须用文档名标注来源，不要输出预检索/结果编号；多来源冲突时并列说明，勿擅自二选一。"
    )
    return "\n".join(lines)


def apply_intent_routing(
    message: str,
    tools: Optional[Sequence[Any]] = None,
    kb_active: bool = True,
    prefetch_query: str = "",
    skill_zone: Any = None,
) -> tuple[IntentDecision, List[Any], str, KbAccessPolicy]:
    """一站式：分类 → 知识库策略 → Skill 分区 → 裁工具 → 提示词。

    skill_zone 可预先由调用方按分区挂载工具后传入；未传则在此解析。
    """
    from orchestrator_integration.skill_zones import resolve_skill_zone, zone_hint

    decision = classify_user_intent(message)
    kb_policy = decide_kb_access_policy(
        message, decision, prefetch_query=prefetch_query
    )
    zone = skill_zone or resolve_skill_zone(message, decision, kb_policy)
    filtered = filter_tools_by_intent(
        list(tools or []),
        decision,
        kb_active=kb_active,
        kb_policy=kb_policy,
        skill_zone=zone,
    )
    hint = build_intent_hint(decision, kb_active=kb_active, kb_policy=kb_policy)
    zh = zone_hint(zone)
    if zh:
        hint = f"{hint}\n\n{zh}" if hint else zh
    return decision, filtered, hint, kb_policy
