"""上下文摘要策略：摘要仅作内部上下文，不进入用户消息列表。

LangChain SummarizationMiddleware 默认把摘要写成 HumanMessage，并可能产出
“Previous conversation was too long to summarize.” 占位文本；本模块：
1. 改写为 SystemMessage（ui_hidden），避免蓝气泡/用户指令污染
2. 压缩失败时回填任务结构化状态（目标/检查点/进度/证据/授权）
3. 提供历史 API / 前端共用的泄漏判定
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from typing_extensions import override

logger = logging.getLogger(__name__)

SUMMARY_LEAK_PREFIX = "Here is a summary of the conversation to date"
SUMMARY_FALLBACK_TOO_LONG = "Previous conversation was too long to summarize."
SUMMARY_LC_SOURCE = "summarization"

TASK_AWARE_SUMMARY_PROMPT = """<role>
Context Extraction Assistant for enterprise task execution
</role>

<primary_objective>
Extract the highest quality context needed to continue the SAME task after history compression.
</primary_objective>

<instructions>
Respond ONLY with the extracted context. Structure with these sections (use "None" if empty):

## SESSION INTENT / TASK GOAL
User's primary goal and active task objective.

## CHECKPOINTS / STAGE PLAN
Current stage, remaining stages/checkpoints, acceptance criteria.

## BUSINESS PROGRESS
Completed vs expected business steps; do NOT treat tool-call counts as business progress.

## EVIDENCE
Qualified evidence already obtained (URLs, screenshots, execution records, claim_level).
List missing evidence blockers if known.

## AUTH / TOOL PREP
Granted skills, allowlist, pending approvals, env readiness gaps.

## ARTIFACTS
Important files/resources created or referenced.

## NEXT STEPS
Concrete next executable actions (not more blind prep loops).

</instructions>

<messages>
Messages to summarize:
{messages}
</messages>"""


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content or "")


def is_summarization_artifact_message(msg: Any = None, *, content: str = "", kwargs: Any = None) -> bool:
    """判定是否为摘要中间件产物（不应展示为用户消息）。"""
    additional = kwargs
    if additional is None and msg is not None:
        additional = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(additional, dict) and (
        additional.get("lc_source") == SUMMARY_LC_SOURCE
        or additional.get("ui_hidden") is True
    ):
        return True
    text = (content or "").strip()
    if not text and msg is not None:
        text = _message_text(msg).strip()
    if not text:
        return False
    if text.startswith(SUMMARY_LEAK_PREFIX):
        return True
    if SUMMARY_FALLBACK_TOO_LONG in text and "summary of the conversation" in text.lower():
        return True
    if text.startswith("[内部上下文摘要"):
        return True
    return False


def _thread_id_from_runtime() -> str:
    try:
        from langgraph.config import get_config

        cfg = get_config() or {}
        configurable = cfg.get("configurable") if isinstance(cfg, dict) else {}
        if isinstance(configurable, dict):
            return str(configurable.get("thread_id") or "")
    except Exception:
        pass
    return ""


def _session_id_from_thread(thread_id: str) -> str:
    """thread_id 形如 userId_projectId_sessionId[_exec_...]。"""
    raw = (thread_id or "").strip()
    if not raw:
        return ""
    # 去掉执行后缀
    if "_exec_" in raw:
        raw = raw.split("_exec_", 1)[0]
    parts = raw.split("_")
    if len(parts) >= 3:
        # session_id 可能本身含下划线：取第三段起全部
        return "_".join(parts[2:])
    return raw


def build_task_structured_snapshot(session_id: str = "", thread_id: str = "") -> str:
    """从 AgentTask 生成可注入摘要的结构化状态。"""
    sid = (session_id or "").strip() or _session_id_from_thread(thread_id or _thread_id_from_runtime())
    if not sid:
        return ""
    try:
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask
        from orchestrator_integration.task_state_closure import public_progress_view

        chat = (
            ChatSession.objects.filter(session_id=sid)
            .order_by("-updated_at")
            .first()
        )
        if chat is None:
            return ""
        task = (
            AgentTask.objects.filter(session=chat)
            .exclude(status__in=["cancelled"])
            .order_by("-updated_at")
            .first()
        )
        if task is None:
            return ""
        view = public_progress_view(task) or {}
        budget = dict(getattr(task, "budget", None) or {})
        auth = dict(getattr(task, "auth_scope", None) or {})
        facts = dict(getattr(task, "facts", None) or {})
        stages = []
        for s in getattr(task, "stage_plan", None) or []:
            if isinstance(s, dict) and s.get("stage_id"):
                stages.append(str(s.get("stage_id")))
        granted = auth.get("granted_skills") or auth.get("effective_allowlist") or []
        evidence_blockers = []
        for b in (facts.get("acceptance_blockers") or [])[:8]:
            if isinstance(b, dict):
                evidence_blockers.append(
                    f"{b.get('blocker_code') or b.get('code') or 'blocker'}:"
                    f"{b.get('step') or b.get('checkpoint') or ''}"
                )
            else:
                evidence_blockers.append(str(b)[:120])
        lines = [
            "## TASK STRUCTURED STATE (authoritative)",
            f"- goal: {getattr(task, 'goal', '') or ''}",
            f"- status: {getattr(task, 'status', '')}",
            f"- current_stage: {getattr(task, 'current_stage', '')}",
            f"- stage_plan: {' > '.join(stages) if stages else 'None'}",
            f"- phase: {view.get('phase') or facts.get('progress_phase') or 'None'}",
            (
                f"- business_progress: "
                f"{budget.get('completed_business_steps', 0)}/"
                f"{budget.get('expected_business_steps', '-')}"
            ),
            (
                f"- tool_budget: "
                f"{budget.get('used_tool_actions', 0)}/"
                f"{budget.get('max_tool_actions', '-')}"
            ),
            f"- granted_skills: {granted if granted else 'None'}",
            f"- evidence_blockers: {evidence_blockers if evidence_blockers else 'None'}",
            (
                f"- pending_timeout_reconcile: "
                f"{'yes' if facts.get('pending_timeout_reconcile') else 'no'}"
            ),
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_task_structured_snapshot skipped: %s", exc)
        return ""


def enrich_summary_text(summary: str, *, session_id: str = "", thread_id: str = "") -> str:
    """为摘要补充结构化任务状态；失败占位时至少保留结构块。"""
    body = (summary or "").strip()
    snap = build_task_structured_snapshot(session_id=session_id, thread_id=thread_id)
    weak = (
        not body
        or body == SUMMARY_FALLBACK_TOO_LONG
        or body.startswith("Error generating summary:")
        or body == "No previous conversation history."
    )
    if snap:
        if weak:
            return (
                "上下文过长未能生成自然语言摘要；以下结构化任务状态必须保留以继续执行：\n"
                f"{snap}"
            )
        if "## TASK STRUCTURED STATE" not in body:
            return f"{body}\n\n{snap}"
    if weak and not snap:
        return (
            "上下文压缩未能生成有效摘要。请勿把本条当作用户指令；"
            "继续执行前须核对任务目标、检查点、业务进度、证据与授权状态。"
        )
    return body


class InternalSummarizationMiddleware(SummarizationMiddleware):
    """摘要写入 SystemMessage，并注入任务结构化状态。"""

    @staticmethod
    def _build_new_messages(summary: str) -> list:
        text = enrich_summary_text(summary)
        return [
            SystemMessage(
                content=f"[内部上下文摘要 — 非用户消息]\n\n{text}",
                additional_kwargs={
                    "lc_source": SUMMARY_LC_SOURCE,
                    "ui_hidden": True,
                },
            )
        ]

    @override
    def _create_summary(self, messages_to_summarize: list) -> str:
        raw = super()._create_summary(messages_to_summarize)
        return enrich_summary_text(raw)

    @override
    async def _acreate_summary(self, messages_to_summarize: list) -> str:
        raw = await super()._acreate_summary(messages_to_summarize)
        return enrich_summary_text(raw)


def filter_history_message_dict(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """历史条目过滤：丢弃摘要泄漏。"""
    if not isinstance(item, dict):
        return item
    content = str(item.get("content") or "")
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if is_summarization_artifact_message(content=content, kwargs=meta):
        return None
    if item.get("type") == "human" and (
        content.startswith(SUMMARY_LEAK_PREFIX)
        or content.startswith("[内部上下文摘要")
    ):
        return None
    return item
