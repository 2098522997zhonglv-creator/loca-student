"""
Agent Loop API 视图 (LangChain v1 重构版)

使用 LangChain v1 的 create_agent + middleware 模式，
替代原有的手动 AgentOrchestrator 循环。

核心变更：
- 使用 create_agent() 统一创建 Agent
- 使用 SummarizationMiddleware 自动处理上下文压缩
- 使用 HumanInTheLoopMiddleware 处理 HITL 审批
- 在流处理层检测工具调用，生成 step_start/step_complete 事件
- 支持 stream 参数控制流式/非流式输出
- SSE 事件格式与旧版保持兼容，前端无需修改
"""

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from django.http import StreamingHttpResponse, JsonResponse

from loca_stude_django.streaming import sse_response
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from asgiref.sync import sync_to_async

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain.agents import create_agent
from loca_stude_django.checkpointer import get_async_checkpointer

from .middleware_config import (
    get_middleware_from_config,
    get_user_tool_approvals,
    get_user_friendly_llm_error,
)
from .playwright_instructions import PLAYWRIGHT_SCRIPT_INSTRUCTION
from .stop_signal import should_stop, clear_stop_signal
from .write_confirmation import (
    build_resume_user_message,
    build_write_confirm_interrupt,
    is_write_confirm_interrupt,
    parse_needs_confirmation,
    get_write_confirm_store,
)
from langgraph_integration.models import ChatSession, LLMConfig
from langgraph_integration.views import (
    _extract_requirement_doc_images_for_message,
    create_llm_instance,
    create_sse_data,
    get_effective_system_prompt_async,
    check_project_permission,
    schedule_auto_summarize_session_title,
)
from projects.models import Project
from prompts.models import UserPrompt
from mcp_tools.models import RemoteMCPConfig
from mcp_tools.persistent_client import mcp_session_manager
from file_management.services import validate_file_ids, build_llm_attachment_context, sync_file_references
from file_management.models import FileReference
from requirements.context_limits import (
    MODEL_CONTEXT_LIMITS,
    context_checker,
    get_context_limit_from_llm,
)

logger = logging.getLogger(__name__)

_KB_PRIORITY_HINT = """
# 工具选择（由你按用户意图决定）
可用工具仅限本轮实际挂载名（见「本轮意图路由」）：通常为 knowledge_search、read_skill_content、execute_skill_script（以及已配置的 MCP）。
禁止把 Skill 名称（如 url-reader、loca-stude、playwright-skill）当成 tool 名直接调用。
若某工具未挂载，说明本轮意图不需要它——不要编造调用结果。

## 怎么选
- 业务事实 / 环境地址 / 规则 / 流程 / 接口设计「文档里怎么写」：优先下方「知识库预检索结果」；不足时再调 knowledge_search。预检索已够用时不要重复 knowledge_search。
- 需要副作用时再调 Skill：读网页、浏览器自动化、保存/写入用例与模块等。
  正确路径：read_skill_content(skill_name) → execute_skill_script(skill_name, command=…)。
- 无副作用的纯问答：直接依据预检索回答并标注来源；不必为了「显得在干活」去调 Skill。

## 多工具顺序与组合
- 有依赖时必须按序：先 read_skill_content，再只读 execute（get_/list_），再写入。
- 多条写入：一次 commands + parallel=false；仅互不依赖的只读/执行才可 parallel=true。
- 执行用例（用户已确认执行）：本轮不要 knowledge_search；同会话内勿重复 read_skill_content；
  优先一条 `execute_testcase --testcase_id N --wait_result`；成功后不要再 get_execution_records；
  同一失败命令无差别重试最多 1 次。

## 自主执行（禁止逼用户写剧本）
- 用户目标含 URL / 加购 / 传图 / 执行用例时：同一轮内连续调用工具直到完成或不可恢复失败。
- 禁止要求用户说「执行」、点名 Skill（browser-use/playwright）、重写逐步指令，或在目标已清晰时二选一确认。
- 工具失败先自愈换策略（如 filechooser 超时改 setInputFiles）；持久会话同一轮内继续带同一 session_id。

## 授权边界（加购 / 下单 / 支付）
- 商品定制、加入购物车、进入结算、提交订单、支付是不同动作；未支付 ≠ 未下单。
- 用户只授权加购时：完成上传+加购并交付真实 cart_id/cart_item_id 等字段与来源；不得为拿 order_sn 而提交订单。
- 「不要支付」只禁止支付，不能当作「允许提交订单」；若必须订单号且尚未授权下单，仅询问是否允许「提交订单」这一新增动作，并保留已完成的加购结果。
- 访问 /cart 或结算 URL 不能单独证明业务动作已发生；知识库订单号示例不得当作本次结果。
- 澄清只在会改变目标/授权/必要输入时提出；已授权的上传与加购不得因订单分支歧义而整单暂停。

## 写入约定（按钮确认）
- 落库用 loca-stude 的 add_testcase 等；没有 functional_test_case_save。
- 写入（create/add/update/delete/set/upload 等）由服务端拦截：未获同意时返回 needs_confirmation，
  界面弹出「确认写入」按钮。不要要求用户打字「确认」；简要列出待写入内容后结束本轮即可。
- 用户点允许后系统会 resume 并放行（user_confirmed=true）；点拒绝则取消写入。
- 只读查询（get_/list_）、浏览器操作、执行用例不需要确认，应自主连续推进，不要中途停下来问「下一步？」。
- 支付类操作除非用户明确要求，否则不要执行；仅写库/支付可打断用户。
- 写入内容里的系统地址、账号、密码、测试数据必须来自用户或知识库原文；没有就写「待补充：xxx」，
  禁止编造 example.com 地址、示例账号或密码来“补充细节”。
- 「看看 / 生成草稿」只输出内容，不落库。
- 多步骤或中文引号：steps 用 --steps_file。

## 引用与诚实
- 事实结论必须用文档名标注来源，禁止在回答里输出 [预检索N]、[结果N] 等内部编号；多来源冲突须并列说明，勿擅自二选一。
- 预检索提示索引为空：如实告知重建索引，不要编造。
- 禁止编造「子代理 / functional_test_case_*」。
- 已注入预检索时，总结不得写「未使用知识库」。
- 账号行为偏好仅作习惯参考，事实以知识库为准。
- Skill（含 url-reader）在本机/部署机执行，可访问内网 192.168.*；禁止编造「我是远程服务所以读不了局域网」。
- 工具失败时只复述工具返回的 error/退出码，不要猜测网络拓扑或让用户改贴公开 URL（除非工具明确超时/连接失败）。

## 知识适用性与证据（FR09）
- 区分文档陈述 / 推断 / 本次观察；仅凭 143/dnd 与分支域名不同不能断言功能不支持，优先核对目标商品实际配置。
- 其他商品「所见所得」观察不能替代用户指定 URL/SKU 的验证；替换目标须说明影响。
- 知识库订单号示例不得填充本次结果；交付真实字段名、值与来源。
- 对用户输出使用任务语言：已做事项、证据、剩余步骤与具体阻塞；禁止要求用户重发路由关键词。
""".strip()

_TASK_LANGUAGE_HINT = """
# 会话语言与输出形态（强制）
- 进度说明与最终答复必须与用户消息同语种；用户使用中文时，禁止用英文旁白（如 I'll / Let me / Now I will call）。
- 调用工具前不要向用户输出英文过程独白；需要提示时用一句中文进度（例：「正在检索知识库…」），不要把过程独白当成最终答复。
- 最终答复只在本轮工具结束后给出一次，使用中文结构化说明：进度、证据、剩余步骤或具体阻塞。
- 进度标题：本轮有真实浏览器/工具业务动作时可用「已完成事项」；若本轮无业务动作，必须用「已整理的输入」，并写明「本轮业务步骤完成数为 0」，禁止制造进度错觉。
- 审批恢复后同样遵守：进度中文、最终答复中文；禁止把工具前英文说明固化成普通回复。
""".strip()


_CITATION_HINT = """
## 引用与冲突约定
- [预检索N]、[结果N] 是内部编号，用户看不到对应内容，回答里禁止出现这类编号。
- 关键事实（地址、账号、规则、接口路径）必须标注来源，用片段的「来源」文档名，格式为「（来源：《文档名》）」。
- 同一段落或列表出自同一文档时只在段末标注一次；不要逐行标注，也不要插在句子中间。
- 若下方出现「来源冲突」提示，必须把不同结论并列写出并标明各自的文档名，不得只保留其中一个。
- 预检索不足时再 knowledge_search；仍无依据则明确说「知识库未找到」，禁止猜测。
""".strip()

_HOST_RE = re.compile(
    r"(?i)\b(?:https?://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)\b"
)


def _normalize_kb_search_params(similarity_threshold=None, top_k=None):
    try:
        threshold = float(similarity_threshold) if similarity_threshold is not None else 0.3
    except (TypeError, ValueError):
        threshold = 0.3
    threshold = min(max(threshold, 0.0), 1.0)
    try:
        k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        k = 5
    k = min(max(k, 1), 20)
    return threshold, k


def _record_tool_behavior_safe(
    user_id: int,
    tool_name: str,
    content: str,
    project_id=None,
) -> None:
    """工具结果落账号行为库；过滤噪声；异常吞掉，不影响主流程。"""
    try:
        from orchestrator_integration.behavior_memory import (
            extract_skill_meta_from_tool_message,
            record_tool_call_behavior,
            should_record_tool_behavior,
        )

        if not should_record_tool_behavior(tool_name or ""):
            return

        skill_meta = extract_skill_meta_from_tool_message(
            tool_name or "", str(content or "")
        )
        record_tool_call_behavior(
            user_id,
            tool_name or "unknown",
            str(content or "")[:500],
            project_id=int(project_id) if project_id else None,
            skill_name=skill_meta.get("skill_name"),
        )
    except Exception as e:
        logger.debug("记录工具行为失败: %s", e)


def _extract_hosts_from_text(text: str) -> set:
    hosts = set()
    for m in _HOST_RE.finditer(text or ""):
        host = (m.group(1) or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if host.count(".") >= 1 and not host.endswith(
            (".png", ".jpg", ".css", ".js", ".svg")
        ):
            hosts.add(host)
    return hosts


def _build_prefetch_conflict_note(chunks: list) -> str:
    """多高分片段指向不同主机时提示冲突（Callie vs Bomiv 类问题）。"""
    host_to_refs = {}
    for idx, item in chunks:
        content = item.get("content") or ""
        score = float(item.get("similarity_score") or 0.0)
        if score < 0.35:
            continue
        meta = item.get("metadata") or {}
        source = meta.get("source") or meta.get("title") or f"预检索{idx}"
        for host in _extract_hosts_from_text(content):
            refs = host_to_refs.setdefault(host, [])
            if f"《{source}》" not in refs:
                refs.append(f"《{source}》")
    if len(host_to_refs) < 2:
        return ""
    detail = "；".join(
        f"{h}←{','.join(refs)}" for h, refs in sorted(host_to_refs.items())
    )
    return (
        f"## 来源冲突提示\n"
        f"预检索命中多个不同站点/主机（{detail}）。"
        f"回答时必须并列说明并标注来源，禁止只保留其中一个。\n"
    )


def _prefetch_knowledge_context(
    knowledge_base_id,
    query: str,
    similarity_threshold: float = 0.3,
    top_k: int = 5,
) -> str:
    """服务端预检索知识库，避免模型不调工具导致「库里有材料却答得蠢」。"""
    if not knowledge_base_id or not (query or "").strip():
        return ""

    from knowledge.models import KnowledgeBase, Document
    from knowledge.services import KnowledgeBaseService, VectorStoreManager
    from collections import Counter

    try:
        knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id)
        service = KnowledgeBaseService(knowledge_base)
        results = service.enhanced_search(
            query.strip(),
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            # 预检索跳过 rewrite + rerank，避免首包被 Reranker 超时拖到 15s+
            enable_rewrite=False,
            enable_rerank=False,
        )
        if results:
            parts = []
            scored_chunks = []
            try:
                from orchestrator_integration.knowledge_claims import (
                    annotate_prefetch_chunk,
                    kb_applicability_footer,
                )
            except Exception:
                annotate_prefetch_chunk = None  # type: ignore
                kb_applicability_footer = lambda: ""  # noqa: E731
            for i, item in enumerate(results[:top_k], 1):
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                score = float(item.get("similarity_score") or 0.0)
                meta = item.get("metadata") or {}
                source = meta.get("source") or meta.get("title") or "未知来源"
                if annotate_prefetch_chunk:
                    parts.append(
                        annotate_prefetch_chunk(
                            content=content, source=source, score=score, index=i
                        )
                    )
                else:
                    parts.append(
                        f"[预检索{i}] (相似度 {score * 100:.1f}%, 来源: {source})\n{content}"
                    )
                scored_chunks.append((i, item))
            if parts:
                conflict = _build_prefetch_conflict_note(scored_chunks)
                return (
                    "## 知识库预检索结果\n"
                    + "\n\n".join(parts)
                    + "\n\n"
                    + conflict
                    + _CITATION_HINT
                    + kb_applicability_footer()
                    + "\n\n（如需更多资料，请继续调用 knowledge_search）"
                )

        counts = dict(
            Counter(
                Document.objects.filter(knowledge_base_id=knowledge_base_id).values_list(
                    "status", flat=True
                )
            )
        )
        points = 0
        try:
            vm = VectorStoreManager(knowledge_base)
            points = vm._collection_points_count(vm._get_collection_name())
        except Exception:
            points = -1

        if points < 0:
            from knowledge.health import build_kb_unavailable_message

            return "## 知识库预检索结果\n" + build_kb_unavailable_message()
        if not counts:
            return "## 知识库预检索结果\n当前知识库没有文档，请先上传后再提问。"
        if counts.get("completed", 0) == 0:
            return (
                "## 知识库预检索结果\n"
                f"没有「已完成」文档（状态分布: {counts}）。请先在知识库中处理/重建索引。"
            )
        if points == 0:
            return (
                "## 知识库预检索结果\n"
                "文档存在但向量索引为空（Qdrant 点数=0）。请在知识库详情执行「重建索引」。"
            )
        return (
            "## 知识库预检索结果\n"
            f"未命中足够相似的片段（阈值={similarity_threshold}, top_k={top_k}, 点数={points}）。"
            "可换关键词，或调用 knowledge_search 换查询语句。"
        )
    except Exception as exc:
        from knowledge.health import build_kb_unavailable_message

        logger.warning("知识库预检索失败 kb=%s: %s", knowledge_base_id, exc)
        return "## 知识库预检索结果\n" + build_kb_unavailable_message(exc)


def _build_sse_error_event(exc: Exception) -> Dict[str, Any]:
    friendly_error = get_user_friendly_llm_error(exc)
    if friendly_error:
        event = {
            "type": "error",
            "message": friendly_error["message"],
            "code": friendly_error["status_code"],
            "error_code": friendly_error["error_code"],
            "errors": friendly_error["errors"],
        }
        if friendly_error.get("model"):
            event["model"] = friendly_error["model"]
        if friendly_error.get("reset_time"):
            event["retry_after"] = friendly_error["reset_time"]
        if friendly_error.get("reset_seconds") is not None:
            event["retry_after_seconds"] = friendly_error["reset_seconds"]
        return event

    return {"type": "error", "message": f"执行错误: {str(exc)}", "code": 500}


# ============== 统一响应辅助函数 ==============


def api_success_response(
    message: str, data: Any = None, code: int = 200
) -> JsonResponse:
    """构建统一格式的成功响应"""
    return JsonResponse(
        {
            "status": "success",
            "code": code,
            "message": message,
            "data": data,
            "errors": None,
        },
        json_dumps_params={"ensure_ascii": False},
    )


def api_error_response(
    message: str, code: int = 400, errors: Any = None
) -> JsonResponse:
    """构建统一格式的错误响应"""
    if errors is None:
        errors = {"detail": [message]}
    return JsonResponse(
        {
            "status": "error",
            "code": code,
            "message": message,
            "data": None,
            "errors": errors,
        },
        status=code,
        json_dumps_params={"ensure_ascii": False},
    )


_MARKDOWN_IMAGE_URL_RE = re.compile(
    r"!\[[^\]]*?\]\((?P<url>https?://[^)\s]+)\)", re.IGNORECASE
)
# 停止于空白、引号、以及紧跟的中文/全角，避免 `xxx.html读这个` 粘连
_PLAIN_HTTP_URL_RE = re.compile(
    r'(?P<url>https?://[^\s<>"\'\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)',
    re.IGNORECASE,
)
_URL_LEADING_WRAP_CHARS = "([<{\"'“‘（【《「『"
_URL_TRAILING_WRAP_CHARS = ")]}>\"'”’）】》」』.,;!?，。；！？、"
_URL_HARD_STOP_CHARS = "\r\n\t ,;)}]>\"'，。；！？、：”’）】》」』"
_IMAGE_URL_PATH_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|bmp|svg|ico)(?:\?|#|$)", re.IGNORECASE
)


def _get_env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        logger.warning(
            "AgentLoopStreamAPI: Invalid int env %s=%s, fallback=%s", name, raw, default
        )
        return default


def _get_env_float(name: str, default: float, min_value: float = 0.1) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(min_value, float(raw))
    except ValueError:
        logger.warning(
            "AgentLoopStreamAPI: Invalid float env %s=%s, fallback=%s",
            name,
            raw,
            default,
        )
        return default


def _normalize_uploaded_image_base64_list(
    raw_images: Any, raw_image: Any = None
) -> List[str]:
    """兼容旧 image 字段与新 images 数组，返回去重后的上传图片列表。"""

    normalized: List[str] = []

    def _append(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        value = candidate.strip()
        if not value or value in normalized:
            return
        normalized.append(value)

    if isinstance(raw_images, list):
        for item in raw_images:
            _append(item)
    else:
        _append(raw_images)

    _append(raw_image)
    return normalized


_LINKED_IMAGE_URL_ALLOWLIST = {
    host.strip().lower()
    for host in os.getenv("AGENT_LOOP_IMAGE_URL_ALLOWLIST", "*").split(",")
    if host.strip()
}
_MAX_LINKED_IMAGES_PER_REQUEST = _get_env_int(
    "AGENT_LOOP_MAX_LINKED_IMAGES", 3, min_value=1
)
_MAX_LINKED_IMAGE_BYTES = _get_env_int(
    "AGENT_LOOP_MAX_LINKED_IMAGE_BYTES", 5 * 1024 * 1024, min_value=1024
)
_LINKED_IMAGE_FETCH_TIMEOUT = _get_env_float(
    "AGENT_LOOP_LINKED_IMAGE_FETCH_TIMEOUT", 8.0, min_value=1.0
)
_MAX_SAFE_TOOL_MESSAGE_CHARS = _get_env_int(
    "AGENT_LOOP_MAX_SAFE_TOOL_MESSAGE_CHARS", 20000, min_value=1000
)


def _extract_linked_image_urls(text: str) -> List[str]:
    """从用户消息中提取图片 URL（支持 Markdown 图片语法和纯 URL）。"""
    if not text:
        return []

    urls: List[str] = []
    seen: set[str] = set()

    def _normalize_url(candidate: str) -> str:
        url = (candidate or "").strip()
        while url and url[0] in _URL_LEADING_WRAP_CHARS:
            url = url[1:]
        hard_stop_indexes = [
            url.find(char) for char in _URL_HARD_STOP_CHARS if char in url
        ]
        if hard_stop_indexes:
            url = url[: min(index for index in hard_stop_indexes if index >= 0)]
        while url and url[-1] in _URL_TRAILING_WRAP_CHARS:
            url = url[:-1]
        # 再剥一层粘连中文（防御：正则未覆盖到的边界）
        url = re.split(r"[\u4e00-\u9fff]", url, maxsplit=1)[0]
        return url.rstrip("/").rstrip(".")

    for pattern in (_MARKDOWN_IMAGE_URL_RE, _PLAIN_HTTP_URL_RE):
        for match in pattern.finditer(text):
            url = _normalize_url(match.group("url") or "")
            if not url or url in seen:
                continue

            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            if parsed.scheme.lower() not in ("http", "https"):
                continue
            if not parsed.netloc:
                continue
            # 仅图片扩展名才进入「拉图」管线，避免把 .html 报告当图片
            path = parsed.path or ""
            if not _IMAGE_URL_PATH_RE.search(path):
                continue

            seen.add(url)
            urls.append(url)

    return urls


def sanitize_prefetch_query(message: str, keep_hosts: bool = True) -> str:
    """预检索 query：整段 URL 换成域名，避免长链接污染向量检索，同时保留「这个地址是什么」的关键词。"""
    text = message or ""
    hosts: List[str] = []

    def _replace(match: re.Match) -> str:
        if keep_hosts:
            try:
                host = (urlparse(match.group("url")).hostname or "").lower()
            except ValueError:
                host = ""
            if host and host not in hosts:
                hosts.append(host)
                return f" {host} "
        return " "

    text = _PLAIN_HTTP_URL_RE.sub(_replace, text)
    return re.sub(r"\s+", " ", text).strip()


def _is_linked_image_url_allowed(url: str) -> bool:
    """校验图片 URL 是否在允许的 host 白名单中（支持 * 全放开）。"""
    if "*" in _LINKED_IMAGE_URL_ALLOWLIST or "all" in _LINKED_IMAGE_URL_ALLOWLIST:
        return True

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").strip().lower()
    return bool(host and host in _LINKED_IMAGE_URL_ALLOWLIST)


async def _download_linked_image_as_data_url(url: str) -> Optional[str]:
    """下载图片 URL 并转为 data URL，供视觉模型消费。"""
    if not _is_linked_image_url_allowed(url):
        logger.warning(
            "AgentLoopStreamAPI: Skip linked image URL not in allowlist. url=%s, allowlist=%s",
            url,
            sorted(_LINKED_IMAGE_URL_ALLOWLIST),
        )
        return None

    timeout = httpx.Timeout(
        _LINKED_IMAGE_FETCH_TIMEOUT, connect=_LINKED_IMAGE_FETCH_TIMEOUT
    )
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "image/*"}
            ) as response:
                response.raise_for_status()
                content_type = (
                    (response.headers.get("Content-Type") or "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if not content_type.startswith("image/"):
                    logger.warning(
                        "AgentLoopStreamAPI: Skip linked URL with non-image content-type. url=%s, content_type=%s",
                        url,
                        content_type or "unknown",
                    )
                    return None

                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    if len(data) + len(chunk) > _MAX_LINKED_IMAGE_BYTES:
                        logger.warning(
                            "AgentLoopStreamAPI: Skip linked image over size limit. url=%s, max_bytes=%s",
                            url,
                            _MAX_LINKED_IMAGE_BYTES,
                        )
                        return None
                    data.extend(chunk)

                if not data:
                    logger.warning(
                        "AgentLoopStreamAPI: Skip empty linked image response. url=%s",
                        url,
                    )
                    return None

                encoded = base64.b64encode(bytes(data)).decode("utf-8")
                return f"data:{content_type};base64,{encoded}"
    except Exception as e:
        logger.warning(
            "AgentLoopStreamAPI: Failed to fetch linked image url=%s, error=%s", url, e
        )
        return None


async def _collect_linked_image_data_urls(
    user_message: str,
    linked_urls: Optional[List[str]] = None,
) -> List[str]:
    """从用户消息 URL 中收集可用图片，并转换为 data URL 列表。"""
    if linked_urls is None:
        linked_urls = _extract_linked_image_urls(user_message)
    if not linked_urls:
        return []

    limited_urls = linked_urls[:_MAX_LINKED_IMAGES_PER_REQUEST]
    if len(linked_urls) > len(limited_urls):
        logger.info(
            "AgentLoopStreamAPI: Truncated linked image URLs from %s to %s",
            len(linked_urls),
            len(limited_urls),
        )

    download_tasks = [_download_linked_image_as_data_url(url) for url in limited_urls]
    results = await asyncio.gather(*download_tasks, return_exceptions=True)

    data_urls: List[str] = []
    for url, result in zip(limited_urls, results):
        if isinstance(result, Exception):
            logger.warning(
                "AgentLoopStreamAPI: Linked image download task failed. url=%s, error=%s",
                url,
                result,
            )
            continue
        if result:
            data_urls.append(result)

    return data_urls


async def _prepare_agent_loop_human_message(
    user_message: str,
    *,
    project: Project,
    supports_vision: bool,
    uploaded_images_base64: Optional[List[str]] = None,
) -> tuple[Any, Dict[str, Any], str]:
    """
    规范化 Agent Loop 的用户消息。

    - 将需求文档中的 `docimg://` 占位符替换为可访问的 `/api/.../images/...` URL
    - 当模型支持视觉时，把需求文档图片与普通 HTTP(S) 图片一并转成多模态输入
    - 返回：LangChain HumanMessage content、additional_kwargs、展示用文本
    """
    display_message = (user_message or "").strip()
    uploaded_images_base64 = uploaded_images_base64 or []

    (
        display_message,
        requirement_doc_image_data_urls,
        requirement_document_id,
    ) = await _extract_requirement_doc_images_for_message(display_message, project)

    if requirement_doc_image_data_urls and not supports_vision:
        logger.warning(
            "AgentLoopStreamAPI: Requirement document images detected but current model does not support vision"
        )

    linked_image_data_urls: List[str] = []
    linked_image_urls = _extract_linked_image_urls(display_message)
    if linked_image_urls:
        logger.info(
            "AgentLoopStreamAPI: Extracted %s candidate linked image URLs from message",
            len(linked_image_urls),
        )
        if supports_vision:
            linked_image_data_urls = await _collect_linked_image_data_urls(
                display_message,
                linked_urls=linked_image_urls,
            )
            if linked_image_data_urls:
                logger.info(
                    "AgentLoopStreamAPI: Loaded %s linked images for multimodal input",
                    len(linked_image_data_urls),
                )
        else:
            logger.warning(
                "AgentLoopStreamAPI: Found %s linked image URLs but current model does not support vision",
                len(linked_image_urls),
            )
    elif "http://" in display_message.lower() or "https://" in display_message.lower():
        logger.debug("AgentLoopStreamAPI: 消息中的 URL 均非图片链接，不做多模态拉图")

    multimodal_image_data_urls = requirement_doc_image_data_urls + linked_image_data_urls

    additional_kwargs: Dict[str, Any] = {}
    if requirement_document_id:
        additional_kwargs["requirement_document_id"] = requirement_document_id
        if requirement_doc_image_data_urls:
            additional_kwargs["image_source"] = "requirement_document"

    if supports_vision and (uploaded_images_base64 or multimodal_image_data_urls):
        human_message_content: Any = [{"type": "text", "text": display_message}]
        for data_url in multimodal_image_data_urls:
            human_message_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )
        for image_base64 in uploaded_images_base64:
            human_message_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    },
                }
            )
    else:
        human_message_content = display_message

    return human_message_content, additional_kwargs, display_message


def process_mcp_tool_output(content: Any, *, tool_name: str = "") -> tuple:
    """
    处理 MCP/检索工具返回内容，统一适配为 ToolResult 文本 + 摘要（FR01.1）。

    Returns:
        tuple: (processed_content, summary)
    """
    from orchestrator_integration.tool_result import adapt_mcp_tool_result

    try:
        tr = adapt_mcp_tool_result(content, tool_name=tool_name or "")
        text = tr.to_tool_text()
        return text, (tr.summary or text)[:200]
    except Exception:
        # 兜底：保持旧行为，避免工具出口中断 SSE
        raw = content
        if isinstance(raw, list) and len(raw) == 1:
            first_item = raw[0]
            if isinstance(first_item, dict) and first_item.get("type") == "text":
                if first_item.get("text") is not None:
                    raw = first_item.get("text")
        if raw is None:
            raw = ""
        elif not isinstance(raw, (str, dict, list, int, float, bool)):
            raw = str(raw)
        if isinstance(raw, str):
            summary = raw[:200]
        else:
            try:
                summary = json.dumps(raw, ensure_ascii=False)[:200]
            except (TypeError, ValueError):
                summary = str(raw)[:200]
        return raw, summary


def _tool_result_sse_events(
    *,
    tool_name: str,
    content: Any,
    step_count: int,
    session_id: str,
    thread_id: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    生成 tool_result SSE，若输出为 needs_confirmation 则追加写库确认 interrupt。
    返回 (events, write_confirm_emitted)。
    """
    content, summary = process_mcp_tool_output(content, tool_name=tool_name or "")
    import hashlib

    eid_src = f"{tool_name}|{step_count}|{summary}"
    event_id = "tr-" + hashlib.sha1(eid_src.encode("utf-8")).hexdigest()[:16]
    events: List[Dict[str, Any]] = [
        {
            "type": "tool_result",
            "event_id": event_id,
            "tool_name": tool_name,
            "tool_output": content,
            "summary": summary,
            "step": step_count,
        }
    ]
    confirmation = parse_needs_confirmation(content)
    if not confirmation:
        return events, False
    events.append(
        build_write_confirm_interrupt(
            session_id=session_id,
            thread_id=thread_id,
            tool_name=tool_name or "execute_skill_script",
            confirmation=confirmation,
        )
    )
    return events, True


def _build_sanitized_messages(messages: List[Any]) -> tuple[List[Any], int]:
    """
    构建合法的消息列表：
    1) 为缺失 ToolMessage 响应的 tool_call 插入占位 ToolMessage
    2) 清理悬空的 ToolMessage（无匹配 tool_call）
    3) 清理有问题的 ToolMessage（content 非字符串 / 过长 / 含 base64）
    返回 (clean_messages, fix_count)
    """
    result: List[Any] = []
    fix_count = 0

    # 当前 pending 的 tool_call IDs 及其工具名
    pending_call_ids: List[str] = []
    pending_call_names: Dict[str, str] = {}

    def _flush_pending() -> None:
        nonlocal fix_count
        for tc_id in pending_call_ids:
            result.append(
                ToolMessage(
                    content="[Tool execution was interrupted]",
                    tool_call_id=tc_id,
                    name=pending_call_names.get(tc_id, "unknown"),
                )
            )
            fix_count += 1
        pending_call_ids.clear()
        pending_call_names.clear()

    def _is_tool_content_problematic(msg: ToolMessage) -> bool:
        content = getattr(msg, "content", None)
        if content is None or not isinstance(content, str):
            return True
        if len(content) > _MAX_SAFE_TOOL_MESSAGE_CHARS:
            return True
        if "data:image/" in content and "base64," in content:
            return True
        return False

    for msg in messages:
        # 带 tool_calls 的 AIMessage
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            _flush_pending()
            result.append(msg)

            for tc in msg.tool_calls:
                tc_id = (
                    tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                )
                tc_name = (
                    tc.get("name")
                    if isinstance(tc, dict)
                    else getattr(tc, "name", None)
                )
                if tc_id:
                    tc_id = str(tc_id)
                    pending_call_ids.append(tc_id)
                    pending_call_names[tc_id] = tc_name or "unknown"
            continue

        # ToolMessage 消息
        if isinstance(msg, ToolMessage):
            tc_id = str(getattr(msg, "tool_call_id", "") or "")
            if tc_id in pending_call_ids:
                pending_call_ids.remove(tc_id)
                if _is_tool_content_problematic(msg):
                    result.append(
                        ToolMessage(
                            content="[Tool output removed: content was invalid or too large]",
                            tool_call_id=tc_id,
                            name=getattr(msg, "name", None)
                            or pending_call_names.get(tc_id, "unknown"),
                        )
                    )
                    fix_count += 1
                else:
                    result.append(msg)
            else:
                # 悬空 ToolMessage，丢弃
                fix_count += 1
            continue

        # 其他消息类型
        _flush_pending()
        result.append(msg)

    _flush_pending()
    return result, fix_count


async def _sanitize_history_before_model_call(
    agent: Any,
    invoke_config: Dict[str, Any],
    log_prefix: str,
) -> Dict[str, Any]:
    """
    统一历史修复入口：读取状态，构建合法消息列表，若有修复则用 REMOVE_ALL + 完整列表覆写。
    """
    try:
        current_state = await agent.aget_state(invoke_config)
    except Exception as e:
        logger.warning("%s: Failed to load state for sanitize: %s", log_prefix, e)
        return {"removed_count": 0, "sanitized": False}

    values = (
        current_state.values
        if hasattr(current_state, "values") and current_state.values
        else {}
    )
    messages = values.get("messages", []) if isinstance(values, dict) else []
    if not messages:
        return {"removed_count": 0, "sanitized": False}

    clean_msgs, fix_count = _build_sanitized_messages(messages)
    if fix_count == 0:
        return {"removed_count": 0, "sanitized": False}

    # 用 REMOVE_ALL + 完整干净列表替换状态
    update_payload = [RemoveMessage(id="__remove_all__"), *clean_msgs]

    available_nodes = list(getattr(agent, "nodes", {}).keys())
    preferred_nodes = [n for n in ("model", "agent", "tools") if n in available_nodes]
    if available_nodes and available_nodes[0] not in preferred_nodes:
        preferred_nodes.append(available_nodes[0])
    preferred_nodes.append(None)

    success = False
    last_error: Optional[Exception] = None
    for as_node in preferred_nodes:
        try:
            if as_node is None:
                await agent.aupdate_state(invoke_config, {"messages": update_payload})
            else:
                await agent.aupdate_state(
                    invoke_config, {"messages": update_payload}, as_node=as_node
                )
            success = True
            logger.info(
                "%s: Sanitized history via REMOVE_ALL, fixed %d issues, %d clean messages (as_node=%s)",
                log_prefix,
                fix_count,
                len(clean_msgs),
                as_node or "auto",
            )
            break
        except Exception as e:
            last_error = e

    if not success:
        logger.error(
            "%s: Failed to sanitize history. fix_count=%d, available_nodes=%s, error=%s",
            log_prefix,
            fix_count,
            available_nodes,
            last_error,
        )
        return {"removed_count": 0, "sanitized": False}

    return {"removed_count": fix_count, "sanitized": True}


def calculate_context_tokens(
    messages: List[Any],
    model_name: str = "gpt-4o",
    tools: Optional[list] = None,
    system_prompt: Optional[str] = None,
) -> tuple[int, int, int]:
    """
    计算当前上下文 Token

    优先使用最后一条带 usage_metadata 的消息；
    如果 provider 未返回 usage_metadata，则回退到内容估算 + 真实开销。

    Args:
        messages: 消息列表
        model_name: 模型名称
        tools: 工具对象列表，用于精确计算开销
        system_prompt: 系统提示词，用于精确计算开销
    """
    # 1) 优先使用 usage_metadata（最准确）
    for msg in reversed(messages):
        if hasattr(msg, "usage_metadata") and msg.usage_metadata:
            usage = msg.usage_metadata
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or (
                input_tokens + output_tokens
            )

            if total_tokens > 0:
                return input_tokens, output_tokens, total_tokens

    # 2) 回退到内容估算 + 真实开销（避免 context_update 始终为 0）
    from orchestrator_integration.middleware_config import _calculate_overhead_tokens

    overhead = _calculate_overhead_tokens(model_name, tools, system_prompt)

    content_tokens = 0
    for msg in messages:
        if hasattr(msg, "content") and msg.content:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            content_tokens += context_checker.count_tokens(content, model_name)

    estimated_total = content_tokens + overhead
    return 0, 0, estimated_total


def _is_unreliable_default_detected_limit(
    model_name: str, detected_limit: Optional[int]
) -> bool:
    """
    判断检测上限是否仅来自未知模型的默认回退值（如 128000）。
    该场景下应优先信任用户配置的 context_limit。
    """
    if not isinstance(detected_limit, int) or detected_limit <= 0:
        return False

    default_limit = int(MODEL_CONTEXT_LIMITS.get("default", 128000))
    if detected_limit != default_limit:
        return False

    normalized_name = (model_name or "").lower()
    for model_key in MODEL_CONTEXT_LIMITS.keys():
        if model_key == "default":
            continue
        if model_key in normalized_name:
            return False

    return True


def resolve_runtime_context_limit(
    config_context_limit: Optional[int], llm, model_name: str
) -> int:
    """
    运行时上下文限制（与 middleware_config 保持一致，用户优先）：
    - 用户配置存在时：直接使用 config
    - 无 config 时：profile > 可靠 detected > 默认值
    """
    config_limit = (
        config_context_limit
        if isinstance(config_context_limit, int) and config_context_limit > 0
        else None
    )
    detected_limit = (
        get_context_limit_from_llm(llm, fallback_model_name=model_name)
        if llm is not None
        else None
    )

    profile_limit = None
    if llm is not None:
        profile = getattr(llm, "profile", None)
        if isinstance(profile, dict):
            max_input_tokens = profile.get("max_input_tokens")
            if isinstance(max_input_tokens, int) and max_input_tokens > 0:
                profile_limit = max_input_tokens

    unreliable_detected_limit = _is_unreliable_default_detected_limit(
        model_name, detected_limit
    )

    if config_limit:
        return config_limit

    if profile_limit:
        return profile_limit

    if (
        isinstance(detected_limit, int)
        and detected_limit > 0
        and not unreliable_detected_limit
    ):
        return detected_limit

    return 128000


@method_decorator(csrf_exempt, name="dispatch")
class AgentLoopStreamAPIView(View):
    """
    Agent Loop 聊天 API (LangChain v1 重构版)

    核心特性：
    - 使用 create_agent() 统一创建 Agent
    - SummarizationMiddleware 自动上下文压缩
    - HumanInTheLoopMiddleware 处理 HITL 审批
    - 在流处理层检测工具调用生成步骤事件
    - 支持 stream 参数：
      - stream=true (默认)：返回 SSE 流式响应
      - stream=false：返回普通 JSON 响应
    """

    # 最大步骤数（用于前端显示）
    MAX_STEPS = 500

    def _update_session_token_usage(
        self, session_id: str, input_tokens: int, output_tokens: int,
        cache_read_tokens: int = 0, user_id=None, project_id=None,
        *,
        duration_ms: int = 0,
    ):
        """更新会话 Token 统计，并扣减 AgentTask 持久化预算（FR07.1/7.2）。"""
        try:
            from django.db.models import F
            from django.utils import timezone

            ChatSession.objects.filter(session_id=session_id).update(
                total_input_tokens=F("total_input_tokens") + input_tokens,
                total_output_tokens=F("total_output_tokens") + output_tokens,
                total_tokens=F("total_tokens") + input_tokens + output_tokens,
                total_cache_read_tokens=F("total_cache_read_tokens") + cache_read_tokens,
                request_count=F("request_count") + 1,
                updated_at=timezone.now(),
            )
        except Exception as e:
            logger.warning(f"Failed to update session token usage: {e}")

        # 写入独立记录（不随会话删除而丢失）
        try:
            from langgraph_integration.models import TokenUsageRecord

            TokenUsageRecord.objects.create(
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cache_read_tokens=cache_read_tokens,
            )
        except Exception as e:
            logger.warning(f"Failed to create TokenUsageRecord: {e}")

        # 持久化任务预算：模型 Token + 本轮耗时
        try:
            from orchestrator_integration.agent_runtime import consume_budget
            from orchestrator_integration.models import AgentTask

            total = int(input_tokens or 0) + int(output_tokens or 0)
            if total <= 0 and duration_ms <= 0:
                return
            chat = ChatSession.objects.filter(session_id=session_id).first()
            if chat is None:
                return
            task = (
                AgentTask.objects.filter(session=chat)
                .exclude(status__in=("completed", "failed", "cancelled"))
                .order_by("-updated_at")
                .first()
            )
            if task is None:
                return
            ok, detail = consume_budget(
                task, tokens=total, duration_ms=max(0, int(duration_ms or 0))
            )
            if not ok:
                logger.warning(
                    "AgentTask token/duration budget exceeded session=%s detail=%s",
                    session_id,
                    detail,
                )
        except Exception as e:
            logger.debug("Failed to consume agent task token budget: %s", e)

    async def authenticate_request(self, request):
        """JWT 认证"""
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationFailed("Authentication credentials were not provided.")

        token = auth_header.split(" ")[1]
        jwt_auth = JWTAuthentication()

        try:
            validated_token = await sync_to_async(jwt_auth.get_validated_token)(token)
            user = await sync_to_async(jwt_auth.get_user)(validated_token)
            return user
        except Exception as e:
            raise AuthenticationFailed(f"Invalid token: {str(e)}")

    async def _create_stream_generator(
        self,
        request,
        user_message: str,
        session_id: str,
        project_id: str,
        project: Project,
        knowledge_base_id: Optional[int] = None,
        use_knowledge_base: bool = True,
        prompt_id: Optional[int] = None,
        uploaded_images_base64: Optional[List[str]] = None,
        generate_playwright_script: bool = False,
        test_case_id: Optional[int] = None,
        use_pytest: bool = True,
        file_ids: Optional[List[int]] = None,
        similarity_threshold: float = 0.3,
        top_k: int = 5,
    ):
        """
        创建 SSE 流式生成器（LangChain v1 重构版）

        使用 create_agent + astream 模式，替代旧的 AgentOrchestrator 循环。
        通过检测 updates 流中的工具调用来生成 step_start/step_complete 事件。
        """
        thread_id = f"{request.user.id}_{project_id}_{session_id}"
        file_ids = file_ids or []
        similarity_threshold, top_k = _normalize_kb_search_params(
            similarity_threshold, top_k
        )

        # 尽早推送 start，避免知识库预检索/Rerank 阻塞导致前端长时间无任何 SSE
        yield create_sse_data(
            {
                "type": "start",
                "session_id": session_id,
                "thread_id": thread_id,
                "project_id": project_id,
                "display_message": user_message,
                "mode": "agent_loop",
                "created_at": None,
            }
        )

        try:
            attached_files = await sync_to_async(validate_file_ids)(file_ids, project, request.user)
            llm_attachment_context = await sync_to_async(build_llm_attachment_context)(attached_files)
            if file_ids:
                await sync_to_async(sync_file_references)(file_ids, project, FileReference.REF_LLM_CHAT, session_id, request.user)
        except Exception as exc:
            yield create_sse_data({"type": "error", "message": f"附件校验失败: {exc}"})
            return

        # 1. 获取 LLM 配置
        try:
            active_config = await sync_to_async(LLMConfig.objects.get)(is_active=True)
            logger.info(f"AgentLoopStreamAPI: Using LLM config: {active_config.name}")
            context_limit = active_config.context_limit or 128000
            model_name = active_config.name or "gpt-4o"
        except LLMConfig.DoesNotExist:
            yield create_sse_data(
                {"type": "error", "message": "No active LLM configuration found"}
            )
            return

        # 2. 验证多模态支持
        if uploaded_images_base64 and not active_config.supports_vision:
            yield create_sse_data(
                {
                    "type": "error",
                    "message": f"模型 {active_config.name} 不支持图片输入",
                }
            )
            return

        try:
            # 3. 初始化 LLM
            llm = await sync_to_async(create_llm_instance)(
                active_config, temperature=0.7
            )
            context_limit = resolve_runtime_context_limit(
                active_config.context_limit, llm, model_name
            )

            # 4. 加载 MCP 工具
            tools: List[Any] = []
            try:
                active_mcp_configs = await sync_to_async(list)(
                    RemoteMCPConfig.objects.filter(is_active=True)
                )
                if active_mcp_configs:
                    client_config = {}
                    for cfg in active_mcp_configs:
                        key = cfg.name or f"remote_{cfg.id}"
                        client_config[key] = {
                            "url": cfg.url,
                            "transport": (cfg.transport or "streamable_http").replace(
                                "-", "_"
                            ),
                        }
                        if cfg.headers:
                            client_config[key]["headers"] = cfg.headers

                    if client_config:
                        mcp_tools = await mcp_session_manager.get_tools_for_config(
                            client_config,
                            user_id=str(request.user.id),
                            project_id=str(project_id),
                            session_id=session_id,
                        )
                        tools.extend(mcp_tools)
                        logger.info(
                            f"AgentLoopStreamAPI: Loaded {len(mcp_tools)} MCP tools"
                        )
                        yield create_sse_data(
                            {
                                "type": "info",
                                "message": f"已加载 {len(mcp_tools)} 个工具",
                            }
                        )
            except Exception as e:
                logger.error(
                    f"AgentLoopStreamAPI: MCP tools loading failed: {e}", exc_info=True
                )
                yield create_sse_data(
                    {"type": "warning", "message": f"MCP 工具加载失败: {str(e)}"}
                )

            # 5. 添加知识库工具
            logger.info(
                f"AgentLoopStreamAPI: 检查知识库工具 - knowledge_base_id={knowledge_base_id}, use_knowledge_base={use_knowledge_base}"
            )
            if knowledge_base_id and use_knowledge_base:
                try:
                    from knowledge.langgraph_integration import create_knowledge_tool

                    logger.info(
                        "AgentLoopStreamAPI: 正在创建知识库工具 threshold=%s top_k=%s",
                        similarity_threshold,
                        top_k,
                    )
                    kb_tool = await sync_to_async(create_knowledge_tool)(
                        knowledge_base_id=knowledge_base_id,
                        user=request.user,
                        similarity_threshold=similarity_threshold,
                        top_k=top_k,
                    )
                    tools.append(kb_tool)
                    logger.info(
                        f"AgentLoopStreamAPI: ✅ 知识库工具已添加: {kb_tool.name}"
                    )
                except Exception as e:
                    logger.warning(
                        f"AgentLoopStreamAPI: ❌ Knowledge tool creation failed: {e}",
                        exc_info=True,
                    )
            else:
                logger.info(
                    f"AgentLoopStreamAPI: ⚠️ 跳过知识库工具 (knowledge_base_id={knowledge_base_id}, use_knowledge_base={use_knowledge_base})"
                )

            # 6. 意图分区 → 按区挂载 Skill 工具（非 LLM 语义选 skill）
            kb_qa_mode = bool(knowledge_base_id and use_knowledge_base)
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
                classify_turn_relation,
                load_open_task_context,
                routing_text_for_classify,
                should_preserve_execution,
                stage_specs_from_prior,
            )

            # FR08.4/08.5：分类与工具裁剪前加载未完成任务（顺序不得颠倒）
            _prior_task = None
            try:
                _prior_task = await sync_to_async(load_open_task_context)(
                    session_id=session_id or "",
                    user_id=int(request.user.id),
                    project_id=int(project_id) if project_id else None,
                )
            except Exception as prior_err:
                logger.warning(
                    "AgentLoopStreamAPI: load prior task failed: %s", prior_err
                )
                _prior_task = None
            _turn_relation = classify_turn_relation(user_message, _prior_task)
            _preserve_exec = should_preserve_execution(_prior_task, _turn_relation)
            _routing_message = routing_text_for_classify(
                user_message, _prior_task, _turn_relation
            )
            logger.info(
                "AgentLoopStreamAPI: turn_relation=%s preserve_exec=%s prior=%s stage=%s",
                _turn_relation,
                _preserve_exec,
                getattr(_prior_task, "public_task_id", None),
                getattr(_prior_task, "current_stage", None),
            )

            user_write_approved = detect_user_write_approval(user_message)
            logger.info(
                "AgentLoopStreamAPI: 本轮写入授权=%s（按用户原话判定）",
                user_write_approved,
            )
            _prefetch_query_early = sanitize_prefetch_query(user_message)
            _prefetch_for_policy = sanitize_prefetch_query(
                user_message, keep_hosts=False
            )
            _early_decision = classify_user_intent(_routing_message)
            _early_kb_policy = decide_kb_access_policy(
                _routing_message,
                _early_decision,
                prefetch_query=_prefetch_for_policy,
            )
            skill_zone = resolve_skill_zone(
                _routing_message,
                _early_decision,
                _early_kb_policy,
                prior_stage=getattr(_prior_task, "current_stage", "") or "",
                prior_auth=getattr(_prior_task, "auth_scope", None) or {},
                turn_relation=_turn_relation,
            )
            from orchestrator_integration.domain_specialists import run_specialist_prep
            from orchestrator_integration.playbooks import (
                playbook_hint,
                resolve_playbook,
            )
            from orchestrator_integration.task_commission import (
                commission_hint,
                commission_task,
            )
            from orchestrator_integration.telemetry_context import (
                TelemetryContext,
                set_telemetry_context,
            )

            # 剧本前置：先命中高频路径，再立项主/辅授权（未命中才走更宽动态面）
            active_playbook = resolve_playbook(
                _routing_message, skill_zone, _early_decision, _early_kb_policy
            )
            task_commission = commission_task(
                message=_routing_message,
                zone=skill_zone,
                playbook=active_playbook,
                decision=_early_decision,
                kb_policy=_early_kb_policy,
            )
            # FR08：尽早生成阶段计划，用于按阶段挂载工具
            from orchestrator_integration.stage_plan import (
                build_stage_plan,
                format_stage_plan_hint,
                stage_mount_policy,
            )

            _stage_specs = build_stage_plan(
                _routing_message,
                _early_decision,
                playbook_id=active_playbook.playbook_id if active_playbook else "",
            )
            # 补充/继续：沿用持久化阶段，避免 QA 立项覆盖浏览器验收口径
            if _preserve_exec and _prior_task is not None:
                prior_specs = stage_specs_from_prior(_prior_task)
                if prior_specs:
                    _stage_specs = prior_specs
            _current_stage_id = (
                (getattr(_prior_task, "current_stage", "") or "")
                if _preserve_exec and _prior_task is not None
                else ""
            ) or (_stage_specs[0].stage_id if _stage_specs else "")
            _stage_mount = stage_mount_policy(_stage_specs, _current_stage_id)
            specialist_spec, specialist_hint_text, specialist_allow = (
                run_specialist_prep(skill_zone, _routing_message)
            )
            # 知识库与执行解耦：已授权未完成执行任务不得因 kb 开启被卸掉
            mount_execute = (
                skill_zone.allow_execute or _preserve_exec or not kb_qa_mode
            )
            # 小工具面 = 任务主类（完整授权）；辅类需 Escape Hatch
            skill_allowlist = task_commission.effective_allowlist()
            if skill_allowlist is None and skill_zone.allow_execute:
                skill_allowlist = skill_zone.effective_allowlist()
            if _preserve_exec:
                # 服务端有界重挂：恢复浏览器/阶段 Skill，并保留元工具纠正通道
                restored = set(zone_skills(ZONE_BROWSER))
                stage_skills = set(_stage_mount.get("skill_names") or [])
                if stage_skills:
                    restored |= stage_skills
                if skill_allowlist is None:
                    skill_allowlist = restored
                else:
                    skill_allowlist = set(skill_allowlist) | restored
            elif not skill_zone.allow_execute and kb_qa_mode:
                skill_allowlist = set()
            if specialist_allow is not None and skill_allowlist is not None:
                skill_allowlist = skill_allowlist & specialist_allow
            elif specialist_allow is not None:
                skill_allowlist = specialist_allow
            # 复合任务：与当前阶段 Skill 能力求交（保留元工具路径）
            if (
                _stage_mount.get("restrict")
                and skill_allowlist is not None
                and not _preserve_exec
            ):
                stage_skills = set(_stage_mount.get("skill_names") or [])
                if stage_skills:
                    skill_allowlist = skill_allowlist & stage_skills or skill_allowlist

            # 短路轮：去掉知识库工具，避免预检索干扰极速路径
            # R17：当前阶段需要 knowledge_search 时不得因 short_circuit 裁掉
            # 补充轮可并行查库，但不得因此卸掉执行工具
            stage_needs_kb = False
            try:
                stage_needs_kb = (
                    "knowledge_search" in set(_stage_mount.get("builtin_tools") or [])
                    or str(_current_stage_id or "") in {"kb_research", "research"}
                    or "knowledge" in set(_stage_mount.get("skill_names") or [])
                    or "knowledge_search"
                    in set(_stage_mount.get("skill_names") or [])
                )
            except Exception:
                stage_needs_kb = False
            if (
                task_commission.short_circuit
                and tools
                and not stage_needs_kb
                and not _preserve_exec
            ):
                tools = [
                    t
                    for t in tools
                    if getattr(t, "name", "") != "knowledge_search"
                ]
                logger.info(
                    "AgentLoopStreamAPI: 剧本短路 short_circuit=True 已移除 knowledge_search "
                    "playbook=%s channel=%s",
                    task_commission.playbook_id,
                    task_commission.channel,
                )
            elif task_commission.short_circuit and stage_needs_kb:
                logger.info(
                    "AgentLoopStreamAPI: 阶段需要知识检索，保留 knowledge_search "
                    "stage=%s playbook=%s",
                    _current_stage_id,
                    task_commission.playbook_id,
                )

            builtin_tools = get_builtin_tools(
                user_id=request.user.id,
                project_id=int(project_id),
                test_case_id=test_case_id,
                chat_session_id=session_id,
                user_write_approved=user_write_approved,
                allowed_skill_names=skill_allowlist,
                allow_execute=mount_execute,
                include_meta_tools=mount_execute,
            )
            tools.extend(builtin_tools)
            # FR08：按阶段裁剪检索类工具（Skill/元工具由 allowlist 控制，此处只关 knowledge_search）
            if _stage_mount.get("restrict"):
                allowed_builtins = set(_stage_mount.get("builtin_tools") or [])
                if "knowledge_search" not in allowed_builtins:
                    tools = [
                        t
                        for t in tools
                        if getattr(t, "name", "") != "knowledge_search"
                    ]
            logger.info(
                "AgentLoopStreamAPI: Added %s builtin tools zone=%s allow_execute=%s "
                "allowlist=%s task=%s short_circuit=%s specialist=%s%s",
                len(builtin_tools),
                skill_zone.zone_id,
                skill_zone.allow_execute,
                sorted(skill_allowlist) if skill_allowlist is not None else None,
                task_commission.task_id,
                task_commission.short_circuit,
                specialist_spec.domain if specialist_spec and specialist_spec.active else None,
                " (与知识库并存)" if kb_qa_mode else "",
            )

            # 6.1 意图路由：按用户话术裁剪工具，减少误调
            intent_decision, tools, intent_hint, kb_policy = apply_intent_routing(
                _routing_message,
                tools,
                kb_active=kb_qa_mode
                and not (task_commission.short_circuit and not _preserve_exec),
                prefetch_query=_prefetch_for_policy,
                skill_zone=skill_zone,
            )
            # 跨轮执行保留：强制确保 execute + 元工具纠正通道存在
            if _preserve_exec and mount_execute:
                have = {getattr(t, "name", "") for t in tools}
                for mt in builtin_tools:
                    name = getattr(mt, "name", "")
                    if name in {
                        "execute_skill_script",
                        "request_tool_expansion",
                        "assert_task_postcondition",
                        "report_task_blocked",
                        "read_skill_content",
                    } and name not in have:
                        tools.append(mt)
                        have.add(name)
            # 短路时强制保留元工具
            if task_commission.short_circuit:
                meta_names = {
                    "request_tool_expansion",
                    "assert_task_postcondition",
                    "report_task_blocked",
                }
                kept_meta = [
                    t for t in builtin_tools if getattr(t, "name", "") in meta_names
                ]
                have = {getattr(t, "name", "") for t in tools}
                for mt in kept_meta:
                    if getattr(mt, "name", "") not in have:
                        tools.append(mt)
            pb_hint = playbook_hint(active_playbook)
            if pb_hint:
                intent_hint = f"{intent_hint}\n\n{pb_hint}" if intent_hint else pb_hint
            ch = commission_hint(task_commission)
            intent_hint = f"{intent_hint}\n\n{ch}" if intent_hint else ch
            stage_hint = format_stage_plan_hint(_stage_specs, _current_stage_id)
            if stage_hint:
                intent_hint = (
                    f"{intent_hint}\n\n{stage_hint}" if intent_hint else stage_hint
                )
            # FR04.5：探测经 finalize 模块统一构造
            try:
                from orchestrator_integration.agent_loop_finalize import (
                    probe_hint_for_message,
                )

                _probe_hint = probe_hint_for_message(user_message)
                if _probe_hint:
                    intent_hint = (
                        f"{intent_hint}\n\n{_probe_hint}"
                        if intent_hint
                        else _probe_hint
                    )
            except Exception as probe_err:
                logger.debug("resource probe skipped: %s", probe_err)
            if specialist_hint_text:
                intent_hint = (
                    f"{intent_hint}\n\n{specialist_hint_text}"
                    if intent_hint
                    else specialist_hint_text
                )
            mounted_names = [getattr(t, "name", str(t)) for t in tools]
            set_telemetry_context(
                TelemetryContext(
                    zone=skill_zone.zone_id,
                    playbook=active_playbook.playbook_id if active_playbook else "",
                    tools_mounted=mounted_names,
                    session_id=session_id or "",
                    user_id=request.user.id,
                    specialist_domain=(
                        specialist_spec.domain
                        if specialist_spec and specialist_spec.active
                        else ""
                    ),
                    commission=task_commission,
                    granted_skills=set(skill_allowlist or []),
                    short_circuit=task_commission.short_circuit,
                    draft_writes=task_commission.draft_writes,
                    current_stage=_current_stage_id,
                )
            )
            try:
                from orchestrator_integration.tool_telemetry import record_route_decision

                record_route_decision(
                    zone=skill_zone.zone_id,
                    playbook=active_playbook.playbook_id if active_playbook else "",
                    intents=sorted(intent_decision.intents),
                    kb_policy=kb_policy.reason,
                    tools_mounted=mounted_names,
                    session_id=session_id or "",
                    user_id=request.user.id,
                )
            except Exception:
                pass
            logger.info(
                "AgentLoopStreamAPI: 意图路由 primary=%s intents=%s zone=%s "
                "playbook=%s task=%s short_circuit=%s channel=%s kb_policy=%s tools=%s allowlist=%s",
                intent_decision.primary,
                sorted(intent_decision.intents),
                skill_zone.zone_id,
                active_playbook.playbook_id if active_playbook else None,
                task_commission.task_id,
                task_commission.short_circuit,
                task_commission.channel,
                kb_policy.reason,
                mounted_names,
                sorted(skill_allowlist) if skill_allowlist is not None else None,
            )
            _kb_cache_hit = False
            _should_record_qa_cache = False

            # 7. 获取或创建 ChatSession（使用 get_or_create 避免竞态条件）
            prompt_obj = None
            if prompt_id:
                try:
                    prompt_obj = await sync_to_async(UserPrompt.objects.get)(
                        id=prompt_id, user=request.user, is_active=True
                    )
                except UserPrompt.DoesNotExist:
                    pass

            chat_session, created = await sync_to_async(
                ChatSession.objects.get_or_create
            )(
                session_id=session_id,
                defaults={
                    "user": request.user,
                    "project": project,
                    "prompt": prompt_obj,
                    "title": f"新对话 - {user_message[:30]}",
                    "resolved_module_key": "llm_chat",
                    "resolved_source": "legacy",
                    "resolved_runtime_mode": "auto",
                },
            )
            if created:
                logger.info(
                    f"AgentLoopStreamAPI: Created new ChatSession: {session_id}"
                )

            # 7.1 FR04/FR08：经 AgentRuntime + RuntimeSession 创建/继续任务
            runtime_task = None
            agent_runtime = None
            runtime_session = None
            try:
                from orchestrator_integration.agent_loop_finalize import RuntimeSession
                from orchestrator_integration.agent_runtime import AgentRuntime
                from orchestrator_integration.stage_plan import infer_auth_scope
                from orchestrator_integration.telemetry_context import (
                    get_telemetry_context,
                    set_telemetry_context,
                )

                stages = [s.to_dict() for s in _stage_specs]
                # 补充轮：授权以持久化为主，本轮仅合并显式新增（create_or_resume 内处理）
                auth_scope = (
                    dict(getattr(_prior_task, "auth_scope", None) or {})
                    if _preserve_exec and _prior_task is not None
                    else infer_auth_scope(user_message)
                )
                if _preserve_exec and _prior_task is not None:
                    # 补充话术可能带出新约束，但不得用本轮短句覆盖原 deny
                    patch_auth = infer_auth_scope(_routing_message)
                    allow = set(auth_scope.get("allow_actions") or [])
                    deny = set(auth_scope.get("deny_actions") or [])
                    allow |= set(patch_auth.get("allow_actions") or [])
                    deny |= set(patch_auth.get("deny_actions") or [])
                    allow -= deny
                    auth_scope["allow_actions"] = sorted(allow)
                    auth_scope["deny_actions"] = sorted(deny)
                agent_runtime = AgentRuntime()
                runtime_task = await sync_to_async(agent_runtime.start)(
                    session=chat_session,
                    user=request.user,
                    project_id=int(project_id) if project_id else None,
                    goal=user_message,
                    commission=task_commission,
                    auth_scope=auth_scope,
                    stage_plan=stages,
                    turn_relation=_turn_relation
                    if "_turn_relation" in locals()
                    else "",
                )
                # R15：未获租约不得构造执行器/派发动作
                if (
                    runtime_task is not None
                    and getattr(runtime_task, "lease_owner", "")
                    != agent_runtime.lease_owner
                ):
                    yield create_sse_data(
                        {
                            "type": "task_status",
                            "error_code": "lease_held",
                            "task_id": runtime_task.public_task_id
                            or str(runtime_task.id),
                            "status": runtime_task.status,
                            "message": "任务正由其他执行者处理，已返回现有状态",
                            "payload": {
                                "lease_owner": runtime_task.lease_owner,
                                "acquired": False,
                            },
                        }
                    )
                    yield create_sse_data({"type": "complete", "status": "lease_held"})
                    return
                runtime_session = RuntimeSession(
                    runtime=agent_runtime,
                    commission=task_commission,
                    stage_specs=_stage_specs,
                    auth_scope=auth_scope,
                )
                # create_or_resume_task 已写入 task_started；此处只向客户端推送，避免重复落库
                yield create_sse_data(
                    {
                        "type": "task_started",
                        "task_id": runtime_task.public_task_id
                        or str(runtime_task.id),
                        "status": "running",
                        "payload": {
                            "stage": runtime_task.current_stage,
                            "stage_plan": stages,
                            "auth_scope": auth_scope,
                        },
                    }
                )
                # 回填 runtime_task_id，供工具结果落库 AgentStep
                try:
                    ctx = get_telemetry_context()
                    ctx.runtime_task_id = int(runtime_task.pk)
                    ctx.current_stage = runtime_task.current_stage or _current_stage_id
                    set_telemetry_context(ctx)
                except Exception:
                    pass
            except Exception as rt_err:
                logger.warning(
                    "AgentLoopStreamAPI: runtime task create failed: %s", rt_err
                )

            # 8. 获取系统提示词（注入 Skills，便于基于知识库生成后写入平台）
            effective_prompt, prompt_source = await get_effective_system_prompt_async(
                request.user,
                prompt_id,
                project,
                include_skills=True,
            )

            # 8.0 意图驱动工具选择提示（始终注入）+ 本轮意图路由
            from orchestrator_integration.evidence_integrity import (
                EVIDENCE_INTEGRITY_HINT,
            )

            effective_prompt = (
                (effective_prompt or "").rstrip()
                + "\n\n"
                + _KB_PRIORITY_HINT
                + "\n\n"
                + _TASK_LANGUAGE_HINT
                + "\n\n"
                + EVIDENCE_INTEGRITY_HINT
                + "\n\n"
                + intent_hint
            ).strip()

            # 8.0a 账号行为偏好预检索（失败静默）
            _prefetch_query = _prefetch_query_early
            _skip_prefetch = bool(
                kb_policy.skip_prefetch or task_commission.short_circuit
            )
            try:
                from orchestrator_integration.behavior_memory import (
                    prefetch_user_behavior_context,
                    record_user_correction_behavior,
                )
                from orchestrator_integration.qa_answer_cache import (
                    invalidate_kb_qa_answers,
                    should_invalidate_for_message,
                )

                await sync_to_async(record_user_correction_behavior)(
                    request.user.id,
                    user_message,
                    project_id=int(project_id) if project_id else None,
                )
                # 纠错：同用户同知识库相近问句缓存失效，并强制本轮查库
                if (
                    knowledge_base_id
                    and use_knowledge_base
                    and should_invalidate_for_message(user_message)
                ):
                    invalidated = await sync_to_async(invalidate_kb_qa_answers)(
                        request.user.id,
                        knowledge_base_id,
                        _prefetch_query or user_message,
                    )
                    logger.info(
                        "AgentLoopStreamAPI: 纠错失效 QA 缓存 count=%s",
                        invalidated,
                    )
                if not _skip_prefetch and _prefetch_query:
                    behavior_prefetch = await sync_to_async(
                        prefetch_user_behavior_context
                    )(request.user.id, _prefetch_query, 5)
                    if behavior_prefetch:
                        effective_prompt = (
                            (effective_prompt or "").rstrip()
                            + "\n\n"
                            + behavior_prefetch
                        ).strip()
                        logger.info(
                            "AgentLoopStreamAPI: 已注入账号行为预检索 context_len=%s",
                            len(behavior_prefetch),
                        )
            except Exception as e:
                logger.warning("AgentLoopStreamAPI: 账号行为记忆跳过: %s", e)

            # 8.0b 启用知识库时：先尝试同用户同库历史答案，再决定是否预检索
            if knowledge_base_id and use_knowledge_base and _skip_prefetch:
                skip_note = {
                    "continue": "本轮为续写指令，已跳过知识库预检索；请基于对话历史继续生成，无需再调 knowledge_search。",
                    "chitchat": "本轮为寒暄/短确认，已跳过知识库；直接简短回复即可。",
                    "meta": "本轮为对上一轮回答的追问，已跳过知识库；请基于对话历史说明，无需 knowledge_search。",
                    "web_only": "本轮以读网页为主且无有效检索词，已跳过知识库预检索。",
                    "write_approval": "本轮为写入确认，已跳过知识库预检索。",
                    "testcase_execute": (
                        "本轮为执行测试用例/自动化：已跳过知识库与精排；禁止 knowledge_search；"
                        "同会话内勿重复 read_skill_content；一条 execute_testcase --wait_result 即可，"
                        "成功后不要再 get_execution_records；失败无差别重试最多 1 次。"
                    ),
                }.get(kb_policy.reason, "本轮已跳过知识库预检索。")
                effective_prompt = (
                    (effective_prompt or "").rstrip()
                    + f"\n\n（{skip_note}）"
                ).strip()
                logger.info(
                    "AgentLoopStreamAPI: 跳过知识库预检索 reason=%s msg=%r",
                    kb_policy.reason,
                    (user_message or "")[:20],
                )
            elif (
                knowledge_base_id
                and use_knowledge_base
                and kb_policy.try_answer_cache
                and not kb_policy.force_kb
                and _prefetch_query
            ):
                try:
                    from orchestrator_integration.qa_answer_cache import (
                        format_cached_answer_context,
                        lookup_kb_qa_answer,
                    )

                    cache_hit = await sync_to_async(lookup_kb_qa_answer)(
                        request.user.id,
                        knowledge_base_id,
                        _prefetch_query,
                    )
                    if cache_hit:
                        _kb_cache_hit = True
                        cached_ctx = format_cached_answer_context(cache_hit)
                        effective_prompt = (
                            (effective_prompt or "").rstrip()
                            + "\n\n"
                            + cached_ctx
                        ).strip()
                        # 命中缓存后去掉 knowledge_search，避免重复打库
                        tools = [
                            t
                            for t in tools
                            if getattr(t, "name", None) != "knowledge_search"
                        ]
                        logger.info(
                            "AgentLoopStreamAPI: QA 缓存命中，跳过预检索 score=%.3f",
                            float(cache_hit.get("score") or 0),
                        )
                        yield create_sse_data(
                            {
                                "type": "status",
                                "message": "命中历史回答，正在生成…",
                            }
                        )
                except Exception as e:
                    logger.warning("AgentLoopStreamAPI: QA 缓存查找跳过: %s", e)

            if (
                knowledge_base_id
                and use_knowledge_base
                and not _skip_prefetch
                and not _kb_cache_hit
                and _prefetch_query
            ):
                yield create_sse_data(
                    {
                        "type": "status",
                        "message": "正在检索知识库…",
                    }
                )
                kb_prefetch = await sync_to_async(_prefetch_knowledge_context)(
                    knowledge_base_id,
                    _prefetch_query,
                    similarity_threshold,
                    top_k,
                )
                _should_record_qa_cache = True
                effective_prompt = (
                    (effective_prompt or "").rstrip()
                    + "\n\n"
                    + (kb_prefetch or "")
                ).strip()
                logger.info(
                    "AgentLoopStreamAPI: 已注入知识库预检索 context_len=%s force=%s",
                    len(kb_prefetch or ""),
                    kb_policy.force_kb,
                )
                yield create_sse_data(
                    {
                        "type": "status",
                        "message": "知识库检索完成，正在生成回答…",
                    }
                )

            # 8.1 如果需要生成脚本，追加脚本生成指令
            if generate_playwright_script:
                effective_prompt = (
                    effective_prompt or ""
                ) + PLAYWRIGHT_SCRIPT_INSTRUCTION
                logger.info(f"AgentLoopStreamAPI: 已追加脚本生成指令")

            # 9. 构建用户消息（支持多模态：上传图片 + HTTP(S) 图片 + 需求文档图片）
            user_message_for_llm = user_message
            if llm_attachment_context:
                user_message_for_llm = user_message + "\n\n以下是用户附加文件内容，请作为本轮对话上下文使用:" + llm_attachment_context
            (
                human_message_content,
                human_message_kwargs,
                display_user_message,
            ) = await _prepare_agent_loop_human_message(
                user_message_for_llm,
                project=project,
                supports_vision=active_config.supports_vision,
                uploaded_images_base64=uploaded_images_base64,
            )
            user_msg = HumanMessage(
                content=human_message_content,
                additional_kwargs=human_message_kwargs,
            )

            # 10. 获取工具名列表用于 HITL
            tool_names = [t.name for t in tools] if tools else None

            # 11. start 已在生成器开头推送；此处仅在有更准确的展示文案时补发一次会话元信息
            if display_user_message and display_user_message != user_message:
                yield create_sse_data(
                    {
                        "type": "start",
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "project_id": project_id,
                        "display_message": display_user_message,
                        "mode": "agent_loop",
                        "created_at": chat_session.created_at.isoformat()
                        if chat_session and chat_session.created_at
                        else None,
                    }
                )

            # 12. 创建 Agent（LangChain v1 统一路径）
            async with get_async_checkpointer() as checkpointer:
                # 获取中间件（需要同步到异步，因为内部有 ORM 查询）
                middleware = await sync_to_async(get_middleware_from_config)(
                    active_config,
                    llm,
                    user=request.user,
                    session_id=session_id,
                    all_tool_names=tool_names,
                    tools=tools,
                    system_prompt=effective_prompt,
                )

                agent = create_agent(
                    llm,
                    tools,
                    system_prompt=effective_prompt,
                    checkpointer=checkpointer,
                    middleware=middleware,
                )
                logger.info(
                    f"AgentLoopStreamAPI: Agent created with {len(tools)} tools"
                )

                # 13. 配置调用参数
                invoke_config = {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 1000,  # 支持约500次工具调用
                }
                input_messages = {"messages": [user_msg]}

                # 13.1 发送前修复历史消息（配对错误 + 风险工具输出）
                await _sanitize_history_before_model_call(
                    agent=agent,
                    invoke_config=invoke_config,
                    log_prefix="AgentLoopStreamAPI",
                )

                # 14. 步骤跟踪状态
                step_count = 0
                current_tool_calls = []
                interrupt_detected = False
                user_stopped = False

                # 15. 流式执行
                stream_modes = ["updates", "messages"]

                try:
                    async for stream_mode, chunk in agent.astream(
                        input_messages, config=invoke_config, stream_mode=stream_modes
                    ):
                        # 检查用户停止信号
                        if should_stop(session_id):
                            user_stopped = True
                            clear_stop_signal(session_id)
                            logger.info(
                                f"AgentLoopStreamAPI: Stop signal received at step {step_count}"
                            )
                            yield create_sse_data(
                                {
                                    "type": "stopped",
                                    "message": "已停止生成",
                                    "step": step_count,
                                }
                            )
                            break

                        if stream_mode == "updates":
                            # 检查中断事件 (HITL)
                            if isinstance(chunk, dict) and "__interrupt__" in chunk:
                                interrupt_info = chunk["__interrupt__"]
                                logger.info(
                                    f"AgentLoopStreamAPI: HITL interrupt detected: {interrupt_info}"
                                )
                                logger.info(
                                    f"AgentLoopStreamAPI: interrupt_info type: {type(interrupt_info)}"
                                )

                                action_requests = []
                                interrupt_id = None
                                # 处理 tuple、list 或单个 Interrupt 对象
                                if isinstance(interrupt_info, (list, tuple)):
                                    interrupts_list = list(interrupt_info)
                                else:
                                    interrupts_list = [interrupt_info]

                                for intr in interrupts_list:
                                    logger.info(
                                        f"AgentLoopStreamAPI: Processing interrupt: type={type(intr)}, dir={dir(intr)}"
                                    )
                                    logger.info(
                                        f"AgentLoopStreamAPI: interrupt repr: {repr(intr)}"
                                    )

                                    if hasattr(intr, "id"):
                                        interrupt_id = intr.id
                                        logger.info(
                                            f"AgentLoopStreamAPI: interrupt_id from attr: {interrupt_id}"
                                        )
                                    elif isinstance(intr, dict) and "id" in intr:
                                        interrupt_id = intr["id"]
                                        logger.info(
                                            f"AgentLoopStreamAPI: interrupt_id from dict: {interrupt_id}"
                                        )

                                    intr_value = (
                                        getattr(intr, "value", intr)
                                        if hasattr(intr, "value")
                                        else intr
                                    )
                                    logger.info(
                                        f"AgentLoopStreamAPI: intr_value type={type(intr_value)}, value={intr_value}"
                                    )

                                    # 尝试多种方式获取 action_requests
                                    ars = []
                                    if isinstance(intr_value, dict):
                                        ars = intr_value.get("action_requests", [])
                                        logger.info(
                                            f"AgentLoopStreamAPI: action_requests from dict: {ars}"
                                        )
                                    elif hasattr(intr_value, "action_requests"):
                                        ars = intr_value.action_requests
                                        logger.info(
                                            f"AgentLoopStreamAPI: action_requests from attr: {ars}"
                                        )

                                    # 如果还是空的，尝试从 intr 本身获取
                                    if not ars and hasattr(intr, "action_requests"):
                                        ars = intr.action_requests
                                        logger.info(
                                            f"AgentLoopStreamAPI: action_requests from intr attr: {ars}"
                                        )

                                    logger.info(
                                        f"AgentLoopStreamAPI: Found {len(ars)} action_requests: {ars}"
                                    )

                                    for ar in ars:
                                        if isinstance(ar, dict):
                                            action_requests.append(
                                                {
                                                    "name": ar.get(
                                                        "name",
                                                        ar.get(
                                                            "action_name", "unknown"
                                                        ),
                                                    ),
                                                    "args": ar.get(
                                                        "arguments", ar.get("args", {})
                                                    ),
                                                    "description": ar.get(
                                                        "description", ""
                                                    ),
                                                }
                                            )
                                        else:
                                            action_requests.append(
                                                {
                                                    "name": getattr(
                                                        ar, "name", "unknown"
                                                    ),
                                                    "args": getattr(
                                                        ar,
                                                        "arguments",
                                                        getattr(ar, "args", {}),
                                                    ),
                                                    "description": getattr(
                                                        ar, "description", ""
                                                    ),
                                                }
                                            )

                                if action_requests:
                                    # 获取用户工具偏好，为 always_reject 的工具添加 auto_reject 标记
                                    user_approvals = await sync_to_async(
                                        get_user_tool_approvals
                                    )(request.user, session_id)
                                    for ar in action_requests:
                                        tool_name = ar.get("name", "")
                                        if (
                                            user_approvals.get(tool_name)
                                            == "always_reject"
                                        ):
                                            ar["auto_reject"] = True
                                            logger.info(
                                                f"AgentLoopStreamAPI: Tool {tool_name} marked as auto_reject"
                                            )

                                    interrupt_detected = True
                                    # R14：先 park/释放租约，再对客户端发布可操作 interrupt
                                    try:
                                        from orchestrator_integration.agent_runtime import (
                                            park_for_approval,
                                        )

                                        if agent_runtime and agent_runtime.task is not None:
                                            await sync_to_async(park_for_approval)(
                                                agent_runtime.task,
                                                interrupt_id=str(
                                                    interrupt_id or id(interrupt_info)
                                                ),
                                            )
                                    except Exception as park_err:
                                        logger.warning(
                                            "native HITL park_for_approval failed: %s",
                                            park_err,
                                        )
                                    yield create_sse_data(
                                        {
                                            "type": "interrupt",
                                            "interrupt_id": interrupt_id
                                            or str(id(interrupt_info)),
                                            "action_requests": action_requests,
                                            "session_id": session_id,
                                            "thread_id": thread_id,
                                        }
                                    )
                                    logger.info(
                                        f"AgentLoopStreamAPI: Sent interrupt with {len(action_requests)} actions"
                                    )

                            # 检测工具调用开始（用于生成 step_start 事件）
                            elif isinstance(chunk, dict):
                                for node_name, node_output in chunk.items():
                                    if node_name == "agent" and isinstance(
                                        node_output, dict
                                    ):
                                        messages = node_output.get("messages", [])
                                        for msg in messages:
                                            if (
                                                hasattr(msg, "tool_calls")
                                                and msg.tool_calls
                                            ):
                                                # 新的工具调用 -> 新步骤开始
                                                step_count += 1
                                                current_tool_calls = msg.tool_calls
                                                tool_names_in_step = [
                                                    tc.get("name", "unknown")
                                                    if isinstance(tc, dict)
                                                    else getattr(tc, "name", "unknown")
                                                    for tc in current_tool_calls
                                                ]
                                                yield create_sse_data(
                                                    {
                                                        "type": "step_start",
                                                        "step": step_count,
                                                        "max_steps": self.MAX_STEPS,
                                                        "tools": tool_names_in_step,
                                                    }
                                                )
                                                logger.info(
                                                    f"AgentLoopStreamAPI: Step {step_count} started with tools: {tool_names_in_step}"
                                                )

                                    elif node_name == "tools" and isinstance(
                                        node_output, dict
                                    ):
                                        # 工具执行完成
                                        tool_messages = node_output.get("messages", [])
                                        for tool_msg in tool_messages:
                                            if hasattr(tool_msg, "content"):
                                                content = tool_msg.content
                                                tool_name = getattr(
                                                    tool_msg, "name", None
                                                ) or getattr(
                                                    tool_msg, "tool_name", "unknown"
                                                )

                                                events, write_confirm = (
                                                    _tool_result_sse_events(
                                                        tool_name=tool_name
                                                        or "unknown",
                                                        content=content,
                                                        step_count=step_count,
                                                        session_id=session_id,
                                                        thread_id=thread_id,
                                                    )
                                                )
                                                if write_confirm:
                                                    logger.info(
                                                        "AgentLoopStreamAPI: Write confirm interrupt emitted"
                                                    )
                                                    # R12/R14：异步边界落库成功并 park 后再发布 interrupt
                                                    try:
                                                        from orchestrator_integration.agent_runtime import (
                                                            park_for_approval,
                                                        )
                                                        from orchestrator_integration.write_confirmation import (
                                                            _persist_approval_request,
                                                        )

                                                        interrupt_evt = next(
                                                            (
                                                                e
                                                                for e in events
                                                                if e.get("type")
                                                                == "interrupt"
                                                            ),
                                                            {},
                                                        )
                                                        pending = (
                                                            (interrupt_evt.get("action_requests") or [{}])[0]
                                                            .get("args", {})
                                                            .get("pending")
                                                            or {}
                                                        )
                                                        await sync_to_async(
                                                            _persist_approval_request
                                                        )(
                                                            interrupt_id=str(
                                                                interrupt_evt.get(
                                                                    "interrupt_id"
                                                                )
                                                                or ""
                                                            ),
                                                            session_id=session_id,
                                                            tool_name=tool_name
                                                            or "execute_skill_script",
                                                            pending=pending
                                                            if isinstance(pending, dict)
                                                            else {},
                                                            summary=str(
                                                                (
                                                                    interrupt_evt.get(
                                                                        "action_requests"
                                                                    )
                                                                    or [{}]
                                                                )[0]
                                                                .get("args", {})
                                                                .get("summary")
                                                                or ""
                                                            ),
                                                            mode=str(
                                                                (
                                                                    interrupt_evt.get(
                                                                        "action_requests"
                                                                    )
                                                                    or [{}]
                                                                )[0]
                                                                .get("args", {})
                                                                .get("mode")
                                                                or "confirm"
                                                            ),
                                                        )
                                                        if (
                                                            agent_runtime
                                                            and agent_runtime.task
                                                            is not None
                                                        ):
                                                            await sync_to_async(
                                                                park_for_approval
                                                            )(
                                                                agent_runtime.task,
                                                                interrupt_id=str(
                                                                    interrupt_evt.get(
                                                                        "interrupt_id"
                                                                    )
                                                                    or ""
                                                                ),
                                                            )
                                                    except Exception as park_err:
                                                        logger.warning(
                                                            "write confirm persist/park failed: %s",
                                                            park_err,
                                                        )
                                                        # 落库失败则阻断发布审批卡
                                                        events = [
                                                            e
                                                            for e in events
                                                            if e.get("type")
                                                            != "interrupt"
                                                        ]
                                                        events.append(
                                                            {
                                                                "type": "error",
                                                                "error_code": "approval_persist_failed",
                                                                "message": "审批落库失败，已阻断写入确认",
                                                            }
                                                        )
                                                for event in events:
                                                    if event.get("type") == "interrupt":
                                                        interrupt_detected = True
                                                    yield create_sse_data(event)
                                                await sync_to_async(
                                                    _record_tool_behavior_safe
                                                )(
                                                    request.user.id,
                                                    tool_name or "unknown",
                                                    str(
                                                        (
                                                            events[0].get(
                                                                "tool_output"
                                                            )
                                                            if events
                                                            else content
                                                        )
                                                        or ""
                                                    ),
                                                    project_id,
                                                )
                                        # 步骤完成
                                        if step_count > 0:
                                            yield create_sse_data(
                                                {
                                                    "type": "step_complete",
                                                    "step": step_count,
                                                }
                                            )

                        elif stream_mode == "messages":
                            # LLM Token 流式输出
                            # messages 模式返回元组 (token, metadata)
                            if isinstance(chunk, tuple) and len(chunk) >= 1:
                                token = chunk[0]
                                meta = (
                                    chunk[1]
                                    if len(chunk) > 1 and isinstance(chunk[1], dict)
                                    else {}
                                )
                                node = (
                                    meta.get("langgraph_node")
                                    or meta.get("checkpoint_ns")
                                    or ""
                                )
                                if node and any(
                                    skip in str(node)
                                    for skip in ("tools", "ToolNode", "summarization")
                                ):
                                    continue
                                if hasattr(token, "content") and token.content:
                                    token_type = type(token).__name__
                                    if "ToolMessage" in token_type:
                                        continue
                                    content = token.content
                                    if isinstance(content, list):
                                        content = "".join(
                                            (
                                                c.get("text", "")
                                                if isinstance(c, dict)
                                                else (c if isinstance(c, str) else "")
                                            )
                                            for c in content
                                        )
                                    if content:
                                        yield create_sse_data(
                                            {"type": "stream", "data": content}
                                        )
                            elif hasattr(chunk, "content") and chunk.content:
                                chunk_type = type(chunk).__name__
                                if "ToolMessage" not in chunk_type:
                                    yield create_sse_data(
                                        {"type": "stream", "data": chunk.content}
                                    )

                except Exception as e:
                    friendly_error = get_user_friendly_llm_error(e)
                    if friendly_error:
                        logger.warning(
                            "AgentLoopStreamAPI: Friendly model error. session_id=%s, error_code=%s, message=%s",
                            session_id,
                            friendly_error.get("error_code"),
                            friendly_error.get("message"),
                        )
                        yield create_sse_data(_build_sse_error_event(e))
                    else:
                        logger.error(
                            "AgentLoopStreamAPI: Streaming error. session_id=%s, thread_id=%s, "
                            "model=%s, error_type=%s, error=%s",
                            session_id,
                            thread_id,
                            model_name,
                            type(e).__name__,
                            e,
                            exc_info=True,
                        )
                        yield create_sse_data(
                            {"type": "error", "message": f"Streaming error: {str(e)}"}
                        )

                # 16. 处理结束状态
                # 无论是否发生 interrupt，都需要计算和发送 context_update
                all_messages = []
                try:
                    current_state = await agent.aget_state(invoke_config)
                    all_messages = (
                        current_state.values.get("messages", [])
                        if current_state.values
                        else []
                    )

                    # 获取当前上下文 token 使用量（优先 usage_metadata，回退估算）
                    input_tokens, output_tokens, total_tokens = (
                        calculate_context_tokens(
                            all_messages, model_name,
                            tools=tools, system_prompt=effective_prompt,
                        )
                    )

                    yield create_sse_data(
                        {
                            "type": "context_update",
                            "context_token_count": total_tokens,
                            "context_limit": context_limit,
                        }
                    )

                    # 记录 Token 使用量到 ChatSession + TokenUsageRecord
                    if input_tokens > 0 or output_tokens > 0:
                        # 提取缓存命中信息
                        cache_read_tokens = 0
                        for msg in reversed(all_messages):
                            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                                cache_info = msg.usage_metadata.get("input_token_details", {})
                                cache_read_tokens = cache_info.get("cache_read", 0) or 0
                                logger.info(
                                    "AgentLoopStreamAPI: Token usage - input=%d, output=%d, cache_read=%d, cache_info=%s",
                                    input_tokens, output_tokens, cache_read_tokens, cache_info,
                                )
                                break
                        else:
                            logger.info(
                                f"AgentLoopStreamAPI: Token usage recorded - input={input_tokens}, output={output_tokens}"
                            )

                        await sync_to_async(self._update_session_token_usage)(
                            session_id, input_tokens, output_tokens,
                            cache_read_tokens=cache_read_tokens,
                            user_id=request.user.id,
                            project_id=int(project_id) if project_id else None,
                        )
                except Exception as e:
                    logger.warning(
                        f"AgentLoopStreamAPI: Failed to calculate token count: {e}"
                    )

                # AI 自动总结会话标题
                if chat_session.title.startswith("新对话") and "llm" in locals() and llm:
                    try:
                        scheduled = schedule_auto_summarize_session_title(
                            llm,
                            chat_session,
                            user_message,
                        )
                        if scheduled:
                            logger.info(f"AgentLoopStreamAPI: Scheduled title auto-summarization for session {session_id}")
                    except Exception as summarize_err:
                        logger.error(f"AgentLoopStreamAPI: Failed to auto-summarize title: {summarize_err}", exc_info=True)

                # 同用户同知识库：本轮若实际查库且产出实质回答，写入答案缓存
                if (
                    not user_stopped
                    and not interrupt_detected
                    and knowledge_base_id
                    and use_knowledge_base
                    and _should_record_qa_cache
                    and not _kb_cache_hit
                ):
                    try:
                        from orchestrator_integration.qa_answer_cache import (
                            extract_sources_from_answer,
                            record_kb_qa_answer,
                        )

                        final_ai_text = ""
                        for msg in reversed(all_messages or []):
                            if isinstance(msg, AIMessage) and not getattr(
                                msg, "tool_calls", None
                            ):
                                content = msg.content
                                if isinstance(content, list):
                                    content = "".join(
                                        (
                                            c.get("text", "")
                                            if isinstance(c, dict)
                                            else (c if isinstance(c, str) else "")
                                        )
                                        for c in content
                                    )
                                final_ai_text = str(content or "").strip()
                                if final_ai_text:
                                    break
                        if final_ai_text:
                            await sync_to_async(record_kb_qa_answer)(
                                request.user.id,
                                knowledge_base_id,
                                _prefetch_query or user_message,
                                final_ai_text,
                                project_id=int(project_id) if project_id else None,
                                sources=extract_sources_from_answer(final_ai_text),
                            )
                    except Exception as cache_err:
                        logger.warning(
                            "AgentLoopStreamAPI: 写入 QA 缓存失败: %s", cache_err
                        )

                # FR03.2：强制验收经 finalize 模块（阶段推进 + AgentRuntime.accept）
                acceptance = None
                if (
                    not user_stopped
                    and not interrupt_detected
                    and "task_commission" in locals()
                    and task_commission is not None
                ):
                    try:
                        from orchestrator_integration.agent_loop_finalize import (
                            RuntimeSession,
                            finalize_task_acceptance,
                        )

                        sess = runtime_session
                        if sess is None and "agent_runtime" in locals() and agent_runtime:
                            sess = RuntimeSession(
                                runtime=agent_runtime,
                                commission=task_commission,
                                stage_specs=_stage_specs
                                if "_stage_specs" in locals()
                                else [],
                                auth_scope={},
                            )
                        if sess is not None:
                            sess.commission = task_commission
                            acceptance, fin_events = await sync_to_async(
                                finalize_task_acceptance
                            )(sess)
                            for fe in fin_events:
                                yield create_sse_data(fe)
                    except Exception as acc_err:
                        logger.warning(
                            "AgentLoopStreamAPI: runtime acceptance failed: %s",
                            acc_err,
                        )

                if user_stopped:
                    yield create_sse_data(
                        {"type": "complete", "status": "stopped", "steps": step_count}
                    )
                elif interrupt_detected:
                    logger.info(
                        "AgentLoopStreamAPI: Interrupt detected, returning early"
                    )
                else:
                    complete_data = {
                        "type": "complete",
                        "total_steps": step_count,
                        "task_status": (
                            "completed"
                            if acceptance and acceptance.get("ok")
                            else (
                                "blocked"
                                if acceptance and not acceptance.get("ok")
                                else "finished"
                            )
                        ),
                    }
                    if acceptance is not None:
                        complete_data["acceptance"] = acceptance
                    if generate_playwright_script:
                        complete_data["script_generation"] = {
                            "enabled": True,
                            "message": "脚本管理工具已启用",
                        }
                    yield create_sse_data(complete_data)

                yield "data: [DONE]\n\n"

        except Exception as e:
            friendly_error = get_user_friendly_llm_error(e)
            if friendly_error:
                logger.warning(
                    "AgentLoopStreamAPI: Friendly model error. session_id=%s, error_code=%s, message=%s",
                    session_id,
                    friendly_error.get("error_code"),
                    friendly_error.get("message"),
                )
                yield create_sse_data(_build_sse_error_event(e))
            else:
                logger.error(
                    "AgentLoopStreamAPI: Error. session_id=%s, thread_id=%s, model=%s, "
                    "error_type=%s, error=%s",
                    session_id,
                    thread_id,
                    model_name if "model_name" in locals() else "unknown",
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                yield create_sse_data(
                    {"type": "error", "message": f"执行错误: {str(e)}"}
                )

    async def post(self, request, *args, **kwargs):
        """
        处理聊天请求

        支持 stream 参数：
        - stream=true (默认)：返回 SSE 流式响应
        - stream=false：返回普通 JSON 响应
        """
        # 1. 认证
        try:
            user = await self.authenticate_request(request)
            request.user = user
        except AuthenticationFailed as e:
            return api_error_response(str(e), 401)

        # 2. 解析请求
        try:
            body_data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError as e:
            return api_error_response(f"Invalid JSON: {e}", 400)

        user_message = body_data.get("message")
        session_id = body_data.get("session_id")
        project_id = body_data.get("project_id")
        knowledge_base_id = body_data.get("knowledge_base_id")
        use_knowledge_base = body_data.get("use_knowledge_base", True)
        similarity_threshold, top_k = _normalize_kb_search_params(
            body_data.get("similarity_threshold", 0.3),
            body_data.get("top_k", 5),
        )
        prompt_id = body_data.get("prompt_id")
        file_ids = body_data.get("file_ids", [])

        # 调试日志：知识库参数
        logger.info(
            "AgentLoopStreamAPI: knowledge_base_id=%s use_knowledge_base=%s threshold=%s top_k=%s",
            knowledge_base_id,
            use_knowledge_base,
            similarity_threshold,
            top_k,
        )
        uploaded_images_base64 = _normalize_uploaded_image_base64_list(
            body_data.get("images"),
            body_data.get("image"),
        )

        # stream 参数：控制流式/非流式输出（默认 true）
        stream_mode = body_data.get("stream", True)
        if isinstance(stream_mode, str):
            stream_mode = stream_mode.lower() in ("true", "1", "yes")

        # Playwright 脚本生成参数
        generate_playwright_script = body_data.get("generate_playwright_script", False)
        test_case_id = body_data.get("test_case_id")  # 用于关联生成的脚本
        use_pytest = body_data.get("use_pytest", True)  # 生成 pytest 格式还是简单格式

        # 兜底：如果前端没传 test_case_id，尝试从消息中解析
        if not test_case_id and user_message:
            import re

            # 匹配 "执行ID为 11 的测试用例" 或 "测试用例 ID：11" 等模式
            match = re.search(
                r"(?:执行\s*ID\s*为|测试用例\s*(?:ID|id)[：:]\s*|case[_-]?id[：:=]\s*)(\d+)",
                user_message,
            )
            if match:
                test_case_id = int(match.group(1))
                logger.info(
                    f"AgentLoopStreamAPI: Parsed test_case_id from message: {test_case_id}"
                )

        # 3. 参数验证
        if not project_id:
            return api_error_response("project_id is required", 400)

        if not user_message:
            return api_error_response("message is required", 400)

        # 4. 项目权限检查
        project = await sync_to_async(check_project_permission)(
            request.user, project_id
        )
        if not project:
            return api_error_response("Project access denied", 403)

        # 5. 生成 session_id
        if not session_id:
            session_id = uuid.uuid4().hex
            logger.info(f"AgentLoopStreamAPI: Generated new session_id: {session_id}")

        msg_preview = (user_message or "")[:80].replace("\n", " ")
        logger.info(
            "LLM_CHAT start user_id=%s project_id=%s session_id=%s kb_id=%s stream=%s msg_len=%s preview=%r",
            user.id,
            project_id,
            session_id,
            knowledge_base_id,
            stream_mode,
            len(user_message or ""),
            msg_preview,
        )

        # 5.1 清理陈旧停止信号，避免上一次"停止"残留影响本轮首次发送
        # 场景：前端先断开 SSE，再调用 stop API，可能导致信号留存到下一次请求
        if clear_stop_signal(session_id):
            logger.info(
                f"AgentLoopStreamAPI: Cleared stale stop signal for session {session_id}"
            )

        # 6. 根据 stream 参数决定响应方式
        if stream_mode:
            # 流式响应 (SSE)
            async def async_generator():
                async for chunk in self._create_stream_generator(
                    request,
                    user_message,
                    session_id,
                    project_id,
                    project,
                    knowledge_base_id,
                    use_knowledge_base,
                    prompt_id,
                    uploaded_images_base64,
                    generate_playwright_script,
                    test_case_id,
                    use_pytest,
                    file_ids,
                    similarity_threshold,
                    top_k,
                ):
                    yield chunk

            return sse_response(async_generator)
        else:
            # 非流式响应 (JSON)
            return await self._handle_non_stream_request(
                request,
                user_message,
                session_id,
                project_id,
                project,
                knowledge_base_id,
                use_knowledge_base,
                prompt_id,
                uploaded_images_base64,
                generate_playwright_script,
                test_case_id,
                use_pytest,
                file_ids,
                similarity_threshold,
                top_k,
            )

    async def _handle_non_stream_request(
        self,
        request,
        user_message: str,
        session_id: str,
        project_id: str,
        project: Project,
        knowledge_base_id: Optional[int] = None,
        use_knowledge_base: bool = True,
        prompt_id: Optional[int] = None,
        uploaded_images_base64: Optional[List[str]] = None,
        generate_playwright_script: bool = False,
        test_case_id: Optional[int] = None,
        use_pytest: bool = True,
        file_ids: Optional[List[int]] = None,
        similarity_threshold: float = 0.3,
        top_k: int = 5,
    ) -> JsonResponse:
        """
        处理非流式请求，收集所有流式事件后返回统一 JSON 响应
        """
        final_content = ""
        final_session_id = session_id
        tool_results = []
        total_steps = 0
        context_token_count = 0
        context_limit = 128000
        error_message = None
        error_status_code = 500
        error_details = None
        interrupt_info = None
        script_generation = None

        try:
            async for chunk in self._create_stream_generator(
                request,
                user_message,
                session_id,
                project_id,
                project,
                knowledge_base_id,
                use_knowledge_base,
                prompt_id,
                uploaded_images_base64,
                generate_playwright_script,
                test_case_id,
                use_pytest,
                file_ids,
                similarity_threshold,
                top_k,
            ):
                # 解析 SSE 数据
                if isinstance(chunk, str) and chunk.startswith("data: "):
                    data_str = chunk[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_str)
                        event_type = event.get("type")

                        if event_type == "start":
                            final_session_id = event.get("session_id", session_id)
                        elif event_type == "stream":
                            # 累积流式内容
                            final_content += event.get("data", "")
                        elif event_type == "tool_result":
                            tool_results.append(
                                {
                                    "summary": event.get("summary", ""),
                                    "tool_output": event.get("tool_output"),
                                    "tool_name": event.get("tool_name"),
                                    "step": event.get("step", 0),
                                }
                            )
                        elif event_type == "step_complete":
                            total_steps = max(total_steps, event.get("step", 0))
                        elif event_type == "context_update":
                            context_token_count = event.get("context_token_count", 0)
                            context_limit = event.get("context_limit", 128000)
                        elif event_type == "error":
                            error_message = event.get("message", "Unknown error")
                            error_status_code = event.get("code", 500)
                            error_details = event.get("errors")
                        elif event_type == "interrupt":
                            interrupt_info = {
                                "interrupt_id": event.get("interrupt_id"),
                                "action_requests": event.get("action_requests", []),
                            }
                        elif event_type == "complete":
                            if event.get("script_generation"):
                                script_generation = event.get("script_generation")
                    except json.JSONDecodeError:
                        continue

            # 构建响应
            if error_message:
                return api_error_response(
                    error_message, error_status_code, error_details
                )

            response_data = {
                "session_id": final_session_id,
                "content": final_content,
                "total_steps": total_steps,
                "tool_results": tool_results,
                "context_token_count": context_token_count,
                "context_limit": context_limit,
            }

            if interrupt_info:
                response_data["interrupt"] = interrupt_info

            if script_generation:
                response_data["script_generation"] = script_generation

            return api_success_response("Chat completed", response_data)

        except Exception as e:
            logger.error(
                "AgentLoopStreamAPI: Non-stream request error. session_id=%s, project_id=%s, "
                "error_type=%s, error=%s",
                session_id,
                project_id,
                type(e).__name__,
                e,
                exc_info=True,
            )
            friendly_error = get_user_friendly_llm_error(e)
            if friendly_error:
                return api_error_response(
                    friendly_error["message"],
                    friendly_error["status_code"],
                    friendly_error["errors"],
                )
            return api_error_response(f"执行错误: {str(e)}", 500)


@method_decorator(csrf_exempt, name="dispatch")
class AgentLoopStopAPIView(View):
    """
    Agent Loop 停止 API

    用于中断正在执行的 Agent Loop 任务。
    """

    async def authenticate_request(self, request):
        """JWT 认证（复用 AgentLoopStreamAPIView 的逻辑）"""
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationFailed("Authentication credentials were not provided.")

        token = auth_header.split(" ")[1]
        jwt_auth = JWTAuthentication()

        try:
            validated_token = await sync_to_async(jwt_auth.get_validated_token)(token)
            user = await sync_to_async(jwt_auth.get_user)(validated_token)
            return user
        except Exception as e:
            raise AuthenticationFailed(f"Invalid token: {str(e)}")

    async def post(self, request, *args, **kwargs):
        """处理停止请求"""
        from .stop_signal import set_stop_signal

        # 1. 认证
        try:
            user = await self.authenticate_request(request)
            request.user = user
        except AuthenticationFailed as e:
            return api_error_response(str(e), 401)

        # 2. 解析请求
        try:
            body_data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError as e:
            return api_error_response(f"Invalid JSON: {e}", 400)

        session_id = body_data.get("session_id")
        if not session_id:
            return api_error_response("session_id is required", 400)

        # 3. 先校验会话归属（FLOW05 / FR05.1），再设停止信号
        cancel_info = {}
        try:
            from orchestrator_integration.agent_runtime import request_cancel
            from orchestrator_integration.models import AgentTask
            from langgraph_integration.models import ChatSession

            chat = await sync_to_async(
                ChatSession.objects.filter(session_id=session_id).first
            )()
            if chat is None:
                return api_error_response("session not found", 404)
            chat_owner_id = getattr(chat, "user_id", None)
            if chat_owner_id is not None and int(chat_owner_id) != int(user.id):
                return api_error_response(
                    "无权停止该会话",
                    403,
                    {"error_code": ["permission_denied"]},
                )

            task = await sync_to_async(
                lambda: AgentTask.objects.filter(session=chat)
                .exclude(status__in=("completed", "failed", "cancelled"))
                .order_by("-updated_at")
                .first()
            )()
            if task is not None:
                if task.user_id is not None and int(task.user_id) != int(user.id):
                    return api_error_response(
                        "无权停止该任务",
                        403,
                        {"error_code": ["permission_denied"]},
                    )
                cancel_info = await sync_to_async(request_cancel)(task)
        except Exception as stop_err:
            logger.debug("stop task cancel mark failed: %s", stop_err)
            return api_error_response(f"停止失败: {stop_err}", 500)

        success = set_stop_signal(session_id)

        logger.info(
            "LLM_CHAT stop user_id=%s session_id=%s success=%s cancel=%s",
            user.id,
            session_id,
            success,
            cancel_info,
        )

        return api_success_response(
            "已发送停止信号",
            {
                "session_id": session_id,
                "success": success,
                "status": cancel_info.get("status") or "cancelling",
                "task_id": cancel_info.get("task_id"),
            },
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentTaskSnapshotAPIView(View):
    """任务快照与事件补取（FR10.3）。"""

    async def authenticate_request(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationFailed("Authentication credentials were not provided.")
        token = auth_header.split(" ")[1]
        jwt_auth = JWTAuthentication()
        validated_token = await sync_to_async(jwt_auth.get_validated_token)(token)
        return await sync_to_async(jwt_auth.get_user)(validated_token)

    async def get(self, request, *args, **kwargs):
        try:
            user = await self.authenticate_request(request)
        except AuthenticationFailed as e:
            return api_error_response(str(e), 401)

        task_id = request.GET.get("task_id") or ""
        session_id = request.GET.get("session_id") or ""
        after_event_id = request.GET.get("after_event_id") or ""
        if not task_id and not session_id:
            return api_error_response("task_id or session_id required", 400)

        try:
            from orchestrator_integration.agent_runtime import task_snapshot
            from orchestrator_integration.models import AgentTask, TaskEvent

            def _load():
                from orchestrator_integration.agent_runtime import assert_task_access

                qs = AgentTask.objects.all()
                if task_id:
                    task = qs.filter(public_task_id=task_id).first() or qs.filter(
                        pk=task_id
                    ).first()
                else:
                    from langgraph_integration.models import ChatSession

                    chat = ChatSession.objects.filter(session_id=session_id).first()
                    if chat is not None and getattr(chat, "user_id", None) not in (
                        None,
                        user.id,
                    ):
                        return "forbidden"
                    task = (
                        AgentTask.objects.filter(session=chat)
                        .order_by("-updated_at")
                        .first()
                        if chat
                        else None
                    )
                if task is None:
                    return None
                # 客户端 project_id 仅校验；权限按任务绑定项目成员资格
                client_project = request.GET.get("project_id")
                access = assert_task_access(
                    task,
                    user_id=user.id,
                    project_id=int(client_project) if client_project else None,
                    session_id=session_id or "",
                )
                if not access.get("ok"):
                    return "forbidden"
                # R19：在数据库按游标过滤后再分页
                snap = task_snapshot(
                    task, after_event_id=after_event_id or "", limit=200
                )
                return snap

            data = await sync_to_async(_load)()
            if data == "forbidden":
                return api_error_response("permission_denied", 403)
            if data is None:
                return api_error_response("task_not_found", 404)
            return api_success_response("ok", data)
        except Exception as e:
            logger.error("AgentTaskSnapshotAPI failed: %s", e, exc_info=True)
            return api_error_response(str(e), 500)


@method_decorator(csrf_exempt, name="dispatch")
class AgentQualityMetricsAPIView(View):
    """质量指标看板数据（FR10.4）。"""

    async def authenticate_request(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationFailed("Authentication credentials were not provided.")
        token = auth_header.split(" ")[1]
        jwt_auth = JWTAuthentication()
        validated_token = await sync_to_async(jwt_auth.get_validated_token)(token)
        return await sync_to_async(jwt_auth.get_user)(validated_token)

    async def get(self, request, *args, **kwargs):
        try:
            user = await self.authenticate_request(request)
        except AuthenticationFailed as e:
            return api_error_response(str(e), 401)
        try:
            from orchestrator_integration.agent_runtime import quality_metrics

            scope = (request.GET.get("scope") or "user").strip()
            limit = int(request.GET.get("limit") or 200)
            uid = None if scope == "all" and getattr(user, "is_staff", False) else user.id
            data = await sync_to_async(quality_metrics)(user_id=uid, limit=limit)
            return api_success_response("ok", data)
        except Exception as e:
            logger.error("AgentQualityMetricsAPI failed: %s", e, exc_info=True)
            return api_error_response(str(e), 500)


@method_decorator(csrf_exempt, name="dispatch")
class AgentLoopResumeAPIView(View):
    """
    Agent Loop Resume API (SSE 流式版)

    用于恢复被 HITL 中断的 Agent Loop 任务。
    接收用户对工具调用的审批决策，然后通过 SSE 流式返回后续执行结果。

    这样前端可以像处理主流一样处理 resume 后的工具执行和 LLM 响应。
    """

    # 最大步骤数（与主流保持一致）
    MAX_STEPS = 500

    async def authenticate_request(self, request):
        """JWT 认证（复用 AgentLoopStreamAPIView 的逻辑）"""
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationFailed("Authentication credentials were not provided.")

        token = auth_header.split(" ")[1]
        jwt_auth = JWTAuthentication()

        try:
            validated_token = await sync_to_async(jwt_auth.get_validated_token)(token)
            user = await sync_to_async(jwt_auth.get_user)(validated_token)
            return user
        except Exception as e:
            raise AuthenticationFailed(f"Invalid token: {str(e)}")

    async def _create_resume_stream_generator(
        self,
        user,
        session_id: str,
        project_id: str,
        resume_data: dict,
        knowledge_base_id: Optional[str] = None,
        use_knowledge_base: bool = False,
    ):
        """
        创建 Resume SSE 流式生成器

        与主流的 _create_stream_generator 类似，但使用 Command(resume=...) 来恢复执行。
        """
        from langgraph.types import Command

        # 1. 解析 resume 数据
        interrupt_id = list(resume_data.keys())[0] if resume_data else None
        if not interrupt_id:
            yield create_sse_data(
                {"type": "error", "message": "Invalid resume data format"}
            )
            return

        decision_info = resume_data[interrupt_id].get("decisions", [{}])[0]
        decision_type = decision_info.get("type", "reject")

        # FR04：经 AgentRuntime 认领租约；已被他人持有则返回现有状态（AC08）
        runtime_task = None
        agent_runtime = None
        try:
            from orchestrator_integration.agent_runtime import AgentRuntime
            from orchestrator_integration.models import AgentTask
            from langgraph_integration.models import ChatSession

            chat = await sync_to_async(
                ChatSession.objects.filter(session_id=session_id).first
            )()
            if chat:
                runtime_task = await sync_to_async(
                    lambda: AgentTask.objects.filter(session=chat)
                    .exclude(status__in=("completed", "failed", "cancelled"))
                    .order_by("-updated_at")
                    .first()
                )()
            if runtime_task is not None:
                agent_runtime = AgentRuntime(
                    lease_owner=f"resume-{uuid.uuid4().hex[:8]}"
                )
                agent_runtime.task = runtime_task
                access = await sync_to_async(agent_runtime.access_ok)(
                    user_id=user.id,
                    project_id=int(project_id) if project_id else None,
                    session_id=session_id,
                )
                if not access.get("ok"):
                    yield create_sse_data(
                        {
                            "type": "error",
                            "error_code": access.get("error_code"),
                            "message": f"无权恢复任务: {access.get('error_code')}",
                        }
                    )
                    return
                claim = await sync_to_async(agent_runtime.claim)(runtime_task)
                if not claim.get("acquired"):
                    yield create_sse_data(
                        {
                            "type": "task_status",
                            "error_code": claim.get("error_code"),
                            "task_id": claim.get("task_id"),
                            "status": claim.get("status"),
                            "message": "任务正由其他执行者处理，已返回现有状态",
                            "payload": claim,
                        }
                    )
                    yield create_sse_data({"type": "complete", "status": "lease_held"})
                    return
                preflight = await sync_to_async(agent_runtime.preflight)()
                if preflight.get("must_reconcile"):
                    yield create_sse_data(
                        {
                            "type": "task_blocked",
                            "task_id": runtime_task.public_task_id
                            or str(runtime_task.id),
                            "status": "blocked",
                            "payload": {
                                "blocker_code": "uncertain_side_effect",
                                "uncertain_steps": preflight.get("uncertain_steps"),
                                "hint": preflight.get("hint"),
                            },
                            "message": preflight.get("hint")
                            or "存在未核对副作用，禁止盲目重放",
                        }
                    )
                    # R10：核对完成前不得继续 resume_start / 消费业务动作
                    yield create_sse_data(
                        {
                            "type": "complete",
                            "status": "blocked",
                            "error_code": "must_reconcile",
                        }
                    )
                    return
        except Exception as lease_err:
            logger.warning("AgentLoopResumeAPI: lease/preflight failed: %s", lease_err)

        # 写库确认按钮：非 LangGraph HITL checkpoint，走新一轮带批准消息的 astream
        write_confirm_resume = is_write_confirm_interrupt(interrupt_id)
        write_confirm_pending = None
        if write_confirm_resume:
            # FR05.4：先消费持久化审批
            consumed: Dict[str, Any] = {"ok": True, "mode": "memory_only"}
            try:
                from orchestrator_integration.write_confirmation import consume_approval

                consumed = await sync_to_async(consume_approval)(
                    interrupt_id,
                    user_id=user.id,
                    decision=decision_type,
                    project_id=int(project_id) if project_id else None,
                    session_id=session_id or "",
                )
                if (
                    not consumed.get("ok")
                    and consumed.get("mode") != "memory_only"
                    and consumed.get("error_code")
                ):
                    yield create_sse_data(
                        {
                            "type": "error",
                            "error_code": consumed.get("error_code"),
                            "message": f"审批无法消费: {consumed.get('error_code')}",
                        }
                    )
                    return
            except Exception as consume_err:
                logger.warning(
                    "AgentLoopResumeAPI: consume_approval failed: %s", consume_err
                )

            write_confirm_pending = get_write_confirm_store().pop(interrupt_id)
            if write_confirm_pending is None and consumed.get("ok") and consumed.get(
                "action_args"
            ):
                write_confirm_pending = {
                    "pending": (consumed.get("action_args") or {}).get("pending"),
                    "mode": (consumed.get("action_args") or {}).get("mode"),
                }
            if write_confirm_pending is None:
                yield create_sse_data(
                    {
                        "type": "error",
                        "message": "写入确认已过期或不存在，请重新发起写入任务",
                    }
                )
                return
            logger.info(
                "AgentLoopResumeAPI: Write confirm resume decision=%s interrupt_id=%s",
                decision_type,
                interrupt_id,
            )

        # 获取工具调用数量（前端传递）
        action_count = resume_data[interrupt_id].get("action_count", 1)

        # 构建 resume 值 - HITL middleware 需要 decisions 格式
        # 为每个 pending 工具调用生成相同的决策
        resume_value = {
            "decisions": [{"type": decision_type} for _ in range(action_count)]
        }

        # 2. 发送 resume 开始信号
        yield create_sse_data(
            {
                "type": "resume_start",
                "session_id": session_id,
                "decision": decision_type,
                "kind": "write_confirmation" if write_confirm_resume else "hitl",
            }
        )

        try:
            from orchestrator_integration.behavior_memory import (
                record_hitl_decision_behavior,
            )

            await sync_to_async(record_hitl_decision_behavior)(
                user.id,
                decision_type,
                project_id=int(project_id) if project_id else None,
            )
        except Exception as e:
            logger.debug("记录 HITL 行为失败: %s", e)

        try:
            async with get_async_checkpointer() as checkpointer:
                # 3. 获取 LLM 配置
                active_config = await sync_to_async(
                    LLMConfig.objects.filter(is_active=True).first
                )()

                if not active_config:
                    yield create_sse_data(
                        {"type": "error", "message": "没有可用的 LLM 配置"}
                    )
                    return

                context_limit = active_config.context_limit or 128000
                model_name = active_config.name or "gpt-4o"
                llm = await sync_to_async(create_llm_instance)(active_config)
                context_limit = resolve_runtime_context_limit(
                    active_config.context_limit, llm, model_name
                )

                # 4. 加载工具
                tools = []

                # 加载 MCP 工具
                try:
                    active_mcp_configs = await sync_to_async(list)(
                        RemoteMCPConfig.objects.filter(is_active=True)
                    )
                    if active_mcp_configs:
                        client_config = {}
                        for cfg in active_mcp_configs:
                            key = cfg.name or f"remote_{cfg.id}"
                            client_config[key] = {
                                "url": cfg.url,
                                "transport": (
                                    cfg.transport or "streamable_http"
                                ).replace("-", "_"),
                            }
                            if cfg.headers:
                                client_config[key]["headers"] = cfg.headers

                        if client_config:
                            mcp_tools = await mcp_session_manager.get_tools_for_config(
                                client_config,
                                user_id=str(user.id),
                                project_id=str(project_id) if project_id else "0",
                                session_id=session_id,
                            )
                            tools.extend(mcp_tools)
                            logger.info(
                                f"AgentLoopResumeAPI: Loaded {len(mcp_tools)} MCP tools"
                            )
                except Exception as e:
                    logger.warning(f"AgentLoopResumeAPI: MCP tools loading failed: {e}")

                # 加载知识库工具
                if knowledge_base_id and use_knowledge_base:
                    try:
                        from knowledge.langgraph_integration import (
                            create_knowledge_tool,
                        )

                        kb_tool = await sync_to_async(create_knowledge_tool)(
                            knowledge_base_id=knowledge_base_id, user=user
                        )
                        tools.append(kb_tool)
                        logger.info(
                            f"AgentLoopResumeAPI: ✅ 知识库工具已添加: {kb_tool.name}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"AgentLoopResumeAPI: ❌ Knowledge tool creation failed: {e}"
                        )

                # 加载内置工具（与知识库并存，便于确认后落库）
                # P0：按未完成任务原授权闭环重挂，禁止把「放行工具」甩给用户
                kb_qa_mode = bool(knowledge_base_id and use_knowledge_base)
                resume_probe_hint = ""
                try:
                    from orchestrator_integration.builtin_tools import (
                        get_builtin_tools,
                    )
                    from orchestrator_integration.skill_zones import (
                        ZONE_BROWSER,
                        zone_skills,
                    )
                    from orchestrator_integration.turn_context import BROWSER_ACTIONS

                    # R13：一次批准不得变成整轮全局写入布尔授权；
                    # 精确动作由 resume 注入消息 + user_confirmed 执行。
                    resume_project_id = int(project_id) if project_id else 0
                    if (
                        not resume_project_id
                        and runtime_task is not None
                        and getattr(runtime_task, "project_id", None)
                    ):
                        resume_project_id = int(runtime_task.project_id)

                    resume_allowlist = None
                    resume_auth = {}
                    resume_stage = ""
                    resume_scope = {}
                    if runtime_task is not None:
                        resume_auth = dict(
                            getattr(runtime_task, "auth_scope", None) or {}
                        )
                        resume_stage = str(
                            getattr(runtime_task, "current_stage", "") or ""
                        )
                        resume_scope = dict(
                            getattr(runtime_task, "task_scope", None) or {}
                        )
                        allow_actions = set(resume_auth.get("allow_actions") or [])
                        is_browser = resume_stage == "browser_interact" or bool(
                            allow_actions & BROWSER_ACTIONS
                        )
                        if is_browser or resume_stage in {
                            "browser_interact",
                            "upload",
                            "cart",
                            "execute",
                            "execute_testcase",
                        }:
                            resume_allowlist = set(zone_skills(ZONE_BROWSER))
                        try:
                            from orchestrator_integration.agent_runtime import (
                                format_probe_hint,
                                probe_browser_resources,
                            )

                            _probe = probe_browser_resources(
                                session_id=str(
                                    resume_scope.get("session_id")
                                    or resume_scope.get("browser_session_id")
                                    or ""
                                ),
                                file_path=str(
                                    resume_scope.get("file_path")
                                    or resume_scope.get("upload_path")
                                    or ""
                                ),
                                target_url=str(resume_scope.get("target_url") or ""),
                            )
                            if _probe and (
                                _probe.get("session_ref")
                                or _probe.get("file_ref")
                                or _probe.get("target_url")
                            ):
                                resume_probe_hint = format_probe_hint(_probe)
                        except Exception as probe_err:
                            logger.warning(
                                "AgentLoopResumeAPI: resume probe failed: %s",
                                probe_err,
                            )

                    builtin_tools = get_builtin_tools(
                        user_id=user.id,
                        project_id=resume_project_id,
                        test_case_id=None,
                        chat_session_id=session_id,
                        user_write_approved=False,
                        allowed_skill_names=resume_allowlist,
                        allow_execute=True,
                        include_meta_tools=True,
                    )
                    tools.extend(builtin_tools)
                    # 确保执行通道存在（即使后续裁剪也不丢）
                    have_names = {getattr(t, "name", "") for t in tools}
                    for mt in list(builtin_tools):
                        name = getattr(mt, "name", "")
                        if name in {
                            "execute_skill_script",
                            "request_tool_expansion",
                            "assert_task_postcondition",
                            "report_task_blocked",
                            "read_skill_content",
                        } and name not in have_names:
                            tools.append(mt)
                            have_names.add(name)
                    logger.info(
                        "AgentLoopResumeAPI: Added %s builtin tools allowlist=%s "
                        "stage=%s%s",
                        len(builtin_tools),
                        sorted(resume_allowlist) if resume_allowlist else None,
                        resume_stage or None,
                        " (与知识库并存)" if kb_qa_mode else "",
                    )
                except Exception as e:
                    logger.warning(
                        f"AgentLoopResumeAPI: Builtin tools loading failed: {e}"
                    )

                # 5. 获取工具名列表和中间件配置
                # 尝试从 ChatSession 关联的 prompt 获取系统提示词，用于精确计算 overhead
                resume_system_prompt = None
                try:
                    chat_session = await sync_to_async(
                        ChatSession.objects.filter(session_id=session_id).select_related("prompt").first
                    )()
                    if chat_session and chat_session.prompt and chat_session.prompt.content:
                        resume_system_prompt = chat_session.prompt.content
                except Exception:
                    pass
                from orchestrator_integration.evidence_integrity import (
                    EVIDENCE_INTEGRITY_HINT,
                    RESUME_EXEC_HINT,
                )

                resume_system_prompt = (
                    (resume_system_prompt or "").rstrip()
                    + "\n\n"
                    + _KB_PRIORITY_HINT
                    + "\n\n"
                    + _TASK_LANGUAGE_HINT
                    + "\n\n"
                    + EVIDENCE_INTEGRITY_HINT
                    + "\n\n"
                    + RESUME_EXEC_HINT
                    + (("\n\n" + resume_probe_hint) if resume_probe_hint else "")
                ).strip()

                tool_names = [t.name for t in tools] if tools else []
                middleware = await sync_to_async(get_middleware_from_config)(
                    active_config,
                    llm,
                    user=user,
                    session_id=session_id,
                    all_tool_names=tool_names,
                    tools=tools,
                    system_prompt=resume_system_prompt,
                )

                # 6. 创建 agent
                agent = create_agent(
                    llm,
                    tools,
                    checkpointer=checkpointer,
                    middleware=middleware,
                )

                # R18：从任务记录补齐 project，复用首次 checkpoint 线程
                resume_pid = project_id
                if (not resume_pid) and runtime_task is not None:
                    resume_pid = getattr(runtime_task, "project_id", None) or ""
                thread_id = (
                    f"{user.id}_{resume_pid}_{session_id}"
                    if resume_pid
                    else session_id
                )
                if runtime_task is not None:
                    try:
                        scope = dict(runtime_task.task_scope or {})
                        if scope.get("checkpoint_ref"):
                            thread_id = str(scope.get("checkpoint_ref"))
                        else:
                            scope["checkpoint_ref"] = thread_id
                            runtime_task.task_scope = scope
                            await sync_to_async(runtime_task.save)(
                                update_fields=["task_scope", "updated_at"]
                            )
                    except Exception:
                        pass
                config = {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 1000,
                }

                # 6.1 恢复执行前，先修复历史消息（配对错误 + 风险工具输出）
                await _sanitize_history_before_model_call(
                    agent=agent,
                    invoke_config=config,
                    log_prefix="AgentLoopResumeAPI",
                )

                # 7. 构建输入：写库确认走新 HumanMessage；否则 Command(resume=...)
                if write_confirm_resume:
                    resume_input: Any = {
                        "messages": [
                            HumanMessage(
                                content=build_resume_user_message(
                                    decision_type, write_confirm_pending
                                )
                            )
                        ]
                    }
                else:
                    resume_input = Command(resume=resume_value)

                # 8. 步骤跟踪状态
                step_count = 0
                interrupt_detected = False

                # 9. 流式执行
                try:
                    async for stream_mode, chunk in agent.astream(
                        resume_input,
                        config=config,
                        stream_mode=["updates", "messages"],
                    ):
                        if stream_mode == "updates":
                            # 检查中断事件 (HITL) - resume 后可能又触发新的中断
                            if isinstance(chunk, dict) and "__interrupt__" in chunk:
                                interrupt_info = chunk["__interrupt__"]
                                logger.info(
                                    f"AgentLoopResumeAPI: HITL interrupt detected after resume: {interrupt_info}"
                                )

                                action_requests = []
                                new_interrupt_id = None

                                if isinstance(interrupt_info, (list, tuple)):
                                    interrupts_list = list(interrupt_info)
                                else:
                                    interrupts_list = [interrupt_info]

                                for intr in interrupts_list:
                                    if hasattr(intr, "id"):
                                        new_interrupt_id = intr.id
                                    elif isinstance(intr, dict) and "id" in intr:
                                        new_interrupt_id = intr["id"]

                                    intr_value = (
                                        getattr(intr, "value", intr)
                                        if hasattr(intr, "value")
                                        else intr
                                    )
                                    if isinstance(intr_value, dict):
                                        ars = intr_value.get("action_requests", [])
                                    elif hasattr(intr_value, "action_requests"):
                                        ars = intr_value.action_requests
                                    else:
                                        ars = []

                                    for ar in ars:
                                        if isinstance(ar, dict):
                                            action_requests.append(
                                                {
                                                    "name": ar.get(
                                                        "name",
                                                        ar.get(
                                                            "action_name", "unknown"
                                                        ),
                                                    ),
                                                    "args": ar.get(
                                                        "arguments", ar.get("args", {})
                                                    ),
                                                    "description": ar.get(
                                                        "description", ""
                                                    ),
                                                }
                                            )
                                        else:
                                            action_requests.append(
                                                {
                                                    "name": getattr(
                                                        ar, "name", "unknown"
                                                    ),
                                                    "args": getattr(
                                                        ar,
                                                        "arguments",
                                                        getattr(ar, "args", {}),
                                                    ),
                                                    "description": getattr(
                                                        ar, "description", ""
                                                    ),
                                                }
                                            )

                                if action_requests:
                                    # 获取用户工具偏好，为 always_reject 的工具添加 auto_reject 标记
                                    user_approvals = await sync_to_async(
                                        get_user_tool_approvals
                                    )(user, session_id)
                                    for ar in action_requests:
                                        tool_name = ar.get("name", "")
                                        if (
                                            user_approvals.get(tool_name)
                                            == "always_reject"
                                        ):
                                            ar["auto_reject"] = True
                                            logger.info(
                                                f"AgentLoopResumeAPI: Tool {tool_name} marked as auto_reject"
                                            )

                                    interrupt_detected = True
                                    yield create_sse_data(
                                        {
                                            "type": "interrupt",
                                            "interrupt_id": new_interrupt_id
                                            or str(id(interrupt_info)),
                                            "action_requests": action_requests,
                                            "session_id": session_id,
                                            "thread_id": thread_id,
                                        }
                                    )

                            # 检测工具调用开始
                            elif isinstance(chunk, dict):
                                for node_name, node_output in chunk.items():
                                    if node_name == "agent" and isinstance(
                                        node_output, dict
                                    ):
                                        messages = node_output.get("messages", [])
                                        for msg in messages:
                                            if (
                                                hasattr(msg, "tool_calls")
                                                and msg.tool_calls
                                            ):
                                                step_count += 1
                                                tool_names_in_step = [
                                                    tc.get("name", "unknown")
                                                    if isinstance(tc, dict)
                                                    else getattr(tc, "name", "unknown")
                                                    for tc in msg.tool_calls
                                                ]
                                                yield create_sse_data(
                                                    {
                                                        "type": "step_start",
                                                        "step": step_count,
                                                        "max_steps": self.MAX_STEPS,
                                                        "tools": tool_names_in_step,
                                                    }
                                                )

                                    elif node_name == "tools" and isinstance(
                                        node_output, dict
                                    ):
                                        tool_messages = node_output.get("messages", [])
                                        for tool_msg in tool_messages:
                                            if hasattr(tool_msg, "content"):
                                                content = tool_msg.content
                                                tool_name = getattr(
                                                    tool_msg, "name", None
                                                ) or getattr(
                                                    tool_msg, "tool_name", "unknown"
                                                )

                                                events, write_confirm = (
                                                    _tool_result_sse_events(
                                                        tool_name=tool_name
                                                        or "unknown",
                                                        content=content,
                                                        step_count=step_count,
                                                        session_id=session_id,
                                                        thread_id=thread_id,
                                                    )
                                                )
                                                for event in events:
                                                    if event.get("type") == "interrupt":
                                                        interrupt_detected = True
                                                    yield create_sse_data(event)
                                                if write_confirm:
                                                    logger.info(
                                                        "AgentLoopResumeAPI: Write confirm interrupt emitted"
                                                    )
                                                    try:
                                                        from orchestrator_integration.agent_runtime import (
                                                            park_for_approval,
                                                        )

                                                        if (
                                                            agent_runtime
                                                            and agent_runtime.task
                                                            is not None
                                                        ):
                                                            interrupt_evt = next(
                                                                (
                                                                    e
                                                                    for e in events
                                                                    if e.get("type")
                                                                    == "interrupt"
                                                                ),
                                                                {},
                                                            )
                                                            await sync_to_async(
                                                                park_for_approval
                                                            )(
                                                                agent_runtime.task,
                                                                interrupt_id=str(
                                                                    interrupt_evt.get(
                                                                        "interrupt_id"
                                                                    )
                                                                    or ""
                                                                ),
                                                            )
                                                    except Exception as park_err:
                                                        logger.warning(
                                                            "resume park_for_approval failed: %s",
                                                            park_err,
                                                        )
                                                await sync_to_async(
                                                    _record_tool_behavior_safe
                                                )(
                                                    user.id,
                                                    tool_name or "unknown",
                                                    str(
                                                        (
                                                            events[0].get(
                                                                "tool_output"
                                                            )
                                                            if events
                                                            else content
                                                        )
                                                        or ""
                                                    ),
                                                    project_id,
                                                )
                                        if step_count > 0:
                                            yield create_sse_data(
                                                {
                                                    "type": "step_complete",
                                                    "step": step_count,
                                                }
                                            )

                        elif stream_mode == "messages":
                            # LLM Token 流式输出
                            # messages 模式返回元组 (token, metadata)
                            if isinstance(chunk, tuple) and len(chunk) >= 1:
                                token = chunk[0]
                                meta = (
                                    chunk[1]
                                    if len(chunk) > 1 and isinstance(chunk[1], dict)
                                    else {}
                                )
                                node = (
                                    meta.get("langgraph_node")
                                    or meta.get("checkpoint_ns")
                                    or ""
                                )
                                if node and any(
                                    skip in str(node)
                                    for skip in ("tools", "ToolNode", "summarization")
                                ):
                                    continue
                                if hasattr(token, "content") and token.content:
                                    token_type = type(token).__name__
                                    if "ToolMessage" in token_type:
                                        continue
                                    content = token.content
                                    if isinstance(content, list):
                                        content = "".join(
                                            (
                                                c.get("text", "")
                                                if isinstance(c, dict)
                                                else (c if isinstance(c, str) else "")
                                            )
                                            for c in content
                                        )
                                    if content:
                                        yield create_sse_data(
                                            {"type": "stream", "data": content}
                                        )
                            elif hasattr(chunk, "content") and chunk.content:
                                chunk_type = type(chunk).__name__
                                if "ToolMessage" not in chunk_type:
                                    yield create_sse_data(
                                        {"type": "stream", "data": chunk.content}
                                    )

                except Exception as e:
                    friendly_error = get_user_friendly_llm_error(e)
                    if friendly_error:
                        logger.warning(
                            "AgentLoopResumeAPI: Friendly model error. session_id=%s, error_code=%s, message=%s",
                            session_id,
                            friendly_error.get("error_code"),
                            friendly_error.get("message"),
                        )
                        yield create_sse_data(_build_sse_error_event(e))
                    else:
                        logger.error(
                            "AgentLoopResumeAPI: Streaming error: %s", e, exc_info=True
                        )
                        yield create_sse_data(
                            {"type": "error", "message": f"Streaming error: {str(e)}"}
                        )

                # 10. 处理结束状态
                # 无论是否发生 interrupt，都需要计算和发送 context_update
                try:
                    current_state = await agent.aget_state(config)
                    all_messages = (
                        current_state.values.get("messages", [])
                        if current_state.values
                        else []
                    )

                    # 获取当前上下文 token 使用量（优先 usage_metadata，回退估算）
                    input_tokens, output_tokens, total_tokens = (
                        calculate_context_tokens(
                            all_messages, model_name,
                            tools=tools, system_prompt=resume_system_prompt,
                        )
                    )

                    yield create_sse_data(
                        {
                            "type": "context_update",
                            "context_token_count": total_tokens,
                            "context_limit": context_limit,
                        }
                    )

                    # 记录 Token 使用量到 ChatSession + TokenUsageRecord
                    if input_tokens > 0 or output_tokens > 0:
                        # 提取缓存命中信息
                        cache_read_tokens = 0
                        for msg in reversed(all_messages):
                            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                                cache_info = msg.usage_metadata.get("input_token_details", {})
                                cache_read_tokens = cache_info.get("cache_read", 0) or 0
                                break

                        await sync_to_async(
                            AgentLoopStreamAPIView()._update_session_token_usage
                        )(
                            session_id, input_tokens, output_tokens,
                            cache_read_tokens=cache_read_tokens,
                            user_id=user.id,
                            project_id=int(project_id) if project_id else None,
                        )
                        logger.info(
                            "AgentLoopResumeAPI: Token usage - input=%d, output=%d, cache_read=%d",
                            input_tokens, output_tokens, cache_read_tokens,
                        )
                except Exception as e:
                    logger.warning(
                        f"AgentLoopResumeAPI: Failed to calculate token count: {e}"
                    )

                if interrupt_detected:
                    logger.info(
                        "AgentLoopResumeAPI: New interrupt detected after resume"
                    )
                else:
                    # R16：resume 与首次共用强制最终验收
                    try:
                        if agent_runtime and agent_runtime.task is not None:
                            from orchestrator_integration.agent_loop_finalize import (
                                RuntimeSession,
                                finalize_task_acceptance,
                            )

                            resume_session = RuntimeSession(
                                runtime=agent_runtime,
                                commission=None,
                                stage_specs=[],
                                auth_scope=getattr(
                                    agent_runtime.task, "auth_scope", None
                                )
                                or {},
                            )
                            # commission 缺失时用任务元数据构造最小收尾
                            class _TmpCommission:
                                pass

                            tmp = _TmpCommission()
                            tmp.post_condition = "resume_finalize"
                            tmp.playbook_id = str(
                                (agent_runtime.task.task_scope or {}).get(
                                    "playbook_id"
                                )
                                or "browser_act"
                            )
                            tmp.last_tool_results = []
                            tmp.task_id = (
                                agent_runtime.task.public_task_id
                                or str(agent_runtime.task.id)
                            )
                            resume_session.commission = tmp
                            acceptance, fin_events = await sync_to_async(
                                finalize_task_acceptance
                            )(resume_session)
                            for fe in fin_events:
                                yield create_sse_data(fe)
                            yield create_sse_data(
                                {
                                    "type": "complete",
                                    "total_steps": step_count,
                                    "decision": decision_type,
                                    "status": (
                                        "completed"
                                        if acceptance.get("ok")
                                        else "blocked"
                                    ),
                                    "acceptance": acceptance,
                                }
                            )
                        else:
                            yield create_sse_data(
                                {
                                    "type": "complete",
                                    "total_steps": step_count,
                                    "decision": decision_type,
                                }
                            )
                    except Exception as fin_err:
                        logger.warning(
                            "AgentLoopResumeAPI: finalize failed: %s", fin_err
                        )
                        yield create_sse_data(
                            {
                                "type": "complete",
                                "total_steps": step_count,
                                "decision": decision_type,
                            }
                        )

                yield "data: [DONE]\n\n"

        except Exception as e:
            friendly_error = get_user_friendly_llm_error(e)
            if friendly_error:
                logger.warning(
                    "AgentLoopResumeAPI: Friendly model error. session_id=%s, error_code=%s, message=%s",
                    session_id,
                    friendly_error.get("error_code"),
                    friendly_error.get("message"),
                )
                yield create_sse_data(_build_sse_error_event(e))
            else:
                logger.exception(
                    "AgentLoopResumeAPI: Error in resume stream for session %s",
                    session_id,
                )
                yield create_sse_data({"type": "error", "message": str(e)})

    async def post(self, request, *args, **kwargs):
        """处理 HITL resume 请求 - 返回 SSE 流式响应"""
        # 1. 认证
        try:
            user = await self.authenticate_request(request)
            request.user = user
        except AuthenticationFailed as e:
            return StreamingHttpResponse(
                iter(
                    [create_sse_data({"type": "error", "message": str(e), "code": 401})]
                ),
                content_type="text/event-stream; charset=utf-8",
                status=401,
            )

        # 2. 解析请求
        try:
            body_data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError as e:
            return StreamingHttpResponse(
                iter(
                    [
                        create_sse_data(
                            {
                                "type": "error",
                                "message": f"Invalid JSON: {e}",
                                "code": 400,
                            }
                        )
                    ]
                ),
                content_type="text/event-stream; charset=utf-8",
                status=400,
            )

        session_id = body_data.get("session_id")
        project_id = body_data.get("project_id")
        resume_data = body_data.get("resume", {})
        # 知识库参数（用于 resume 时重新加载知识库工具）
        knowledge_base_id = body_data.get("knowledge_base_id")
        use_knowledge_base = body_data.get("use_knowledge_base", False)

        if not session_id:
            return StreamingHttpResponse(
                iter(
                    [
                        create_sse_data(
                            {
                                "type": "error",
                                "message": "session_id is required",
                                "code": 400,
                            }
                        )
                    ]
                ),
                content_type="text/event-stream; charset=utf-8",
                status=400,
            )

        if not resume_data:
            return StreamingHttpResponse(
                iter(
                    [
                        create_sse_data(
                            {
                                "type": "error",
                                "message": "resume data is required",
                                "code": 400,
                            }
                        )
                    ]
                ),
                content_type="text/event-stream; charset=utf-8",
                status=400,
            )

        logger.info(
            "LLM_CHAT resume user_id=%s project_id=%s session_id=%s kb_id=%s",
            user.id,
            project_id,
            session_id,
            knowledge_base_id,
        )

        # 3. 返回 SSE 流式响应
        async def async_generator():
            async for chunk in self._create_resume_stream_generator(
                user,
                session_id,
                project_id,
                resume_data,
                knowledge_base_id,
                use_knowledge_base,
            ):
                yield chunk

        return sse_response(async_generator)
