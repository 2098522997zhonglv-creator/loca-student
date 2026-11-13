"""跨轮任务上下文（FR08.4 / FR08.5）：分类与工具裁剪前加载未完成任务。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

TURN_NONE = "none"
TURN_CONTINUE = "continue"
TURN_SUPPLEMENT = "supplement"
TURN_NEW = "new_question"
TURN_CANCEL = "cancel"

_OPEN_STATUSES = (
    "pending",
    "running",
    "awaiting_approval",
    "blocked",
    "paused",
    "cancelling",
)

_CONTINUE_CUES = ("继续", "接着", "剩下的", "复用 session_id", "按上面的方案")
_CANCEL_CUES = ("取消任务", "停止任务", "终止任务", "算了不做了", "不要继续了")
_SUPPLEMENT_CUES = (
    "测试环境",
    "生产环境",
    "预发",
    "预发布",
    "美站",
    "欧站",
    "中站",
    "站点",
    "后台",
    "环境",
    "账号",
    "店铺",
    "用测试",
    "用生产",
    "选美",
    "选欧",
    "选中",
)
_NEW_QUESTION_MARKERS = (
    "另外问",
    "另问",
    "新任务",
    "换个问题",
    "别的问题",
    "无关的",
    "先问一下",
)
_PURE_QA_TAIL = re.compile(
    r"(是什么|是啥|什么意思|怎么理解|如何理解|为什么|解释一下|有什么区别)[？?\s]*$"
)
_EXEC_STAGES = frozenset(
    {
        "browser_interact",
        "execute",
        "execute_testcase",
        "persist",
        "generate_draft",
    }
)
BROWSER_ACTIONS = frozenset(
    {"upload", "add_to_cart", "submit_order", "checkout", "pay"}
)


@dataclass
class PriorTaskContext:
    """用户/会话范围内当前未完成任务快照。"""

    task_pk: int
    public_task_id: str
    goal: str
    current_stage: str
    stage_plan: List[Dict[str, Any]] = field(default_factory=list)
    auth_scope: Dict[str, Any] = field(default_factory=dict)
    task_scope: Dict[str, Any] = field(default_factory=dict)
    status: str = ""
    session_id: str = ""

    @property
    def allow_actions(self) -> Set[str]:
        return set((self.auth_scope or {}).get("allow_actions") or [])

    @property
    def is_browser_authorized(self) -> bool:
        if self.current_stage == "browser_interact":
            return True
        return bool(self.allow_actions & BROWSER_ACTIONS)

    @property
    def is_executable_stage(self) -> bool:
        if self.current_stage in _EXEC_STAGES:
            return True
        return bool(self.allow_actions)


def load_open_task_context(
    *,
    session_id: str,
    user_id: int,
    project_id: Optional[int] = None,
) -> Optional[PriorTaskContext]:
    """按 session（及用户/项目）加载最近一条未完成任务。"""
    if not session_id or not user_id:
        return None
    from langgraph_integration.models import ChatSession
    from orchestrator_integration.models import AgentTask

    chat = (
        ChatSession.objects.filter(session_id=session_id, user_id=user_id)
        .order_by("-updated_at")
        .first()
    )
    if chat is None:
        chat = ChatSession.objects.filter(session_id=session_id).first()
        if chat is None or int(getattr(chat, "user_id", 0) or 0) != int(user_id):
            return None

    from django.db.models import Q

    qs = AgentTask.objects.filter(session=chat, status__in=_OPEN_STATUSES)
    if project_id is not None:
        qs = qs.filter(Q(project_id=project_id) | Q(project_id__isnull=True))
    task = qs.order_by("-updated_at").first()
    if task is None:
        # 同用户同项目兜底：无 session 关联时仍可挂最近开放任务
        qs2 = AgentTask.objects.filter(user_id=user_id, status__in=_OPEN_STATUSES)
        if project_id is not None:
            qs2 = qs2.filter(project_id=project_id)
        task = qs2.order_by("-updated_at").first()
        if task is None:
            return None

    return PriorTaskContext(
        task_pk=int(task.pk),
        public_task_id=str(task.public_task_id or task.pk),
        goal=str(task.goal or ""),
        current_stage=str(task.current_stage or ""),
        stage_plan=list(task.stage_plan or []),
        auth_scope=dict(task.auth_scope or {}),
        task_scope=dict(task.task_scope or {}),
        status=str(task.status or ""),
        session_id=str(getattr(chat, "session_id", "") or session_id),
    )


def classify_turn_relation(
    message: str, prior: Optional[PriorTaskContext]
) -> str:
    """识别本轮相对未完成任务的关系：继续 / 补充 / 新问题 / 取消。"""
    text = (message or "").strip()
    if not prior:
        return TURN_NONE
    if any(k in text for k in _CANCEL_CUES):
        return TURN_CANCEL
    if any(k in text for k in _CONTINUE_CUES):
        return TURN_CONTINUE
    if any(k in text for k in _NEW_QUESTION_MARKERS):
        return TURN_NEW
    if not prior.is_executable_stage:
        return TURN_NEW

    supplement_hit = any(k in text for k in _SUPPLEMENT_CUES)
    pure_qa = bool(_PURE_QA_TAIL.search(text)) and not supplement_hit
    if pure_qa and len(text) > 12:
        return TURN_NEW
    if supplement_hit:
        return TURN_SUPPLEMENT
    # 短补参、无新目标动词 → 视为对原任务的约束补充（不必含「继续」）
    if len(text) <= 80 and not re.search(
        r"https?://|帮我|请你|执行用例|生成用例|保存入库", text
    ):
        return TURN_SUPPLEMENT
    # 默认同会话开放执行任务：优先复用，避免误开新 QA 任务
    return TURN_SUPPLEMENT


def should_preserve_execution(
    prior: Optional[PriorTaskContext], relation: str
) -> bool:
    if prior is None:
        return False
    if relation not in {TURN_CONTINUE, TURN_SUPPLEMENT}:
        return False
    return prior.is_executable_stage


def routing_text_for_classify(
    message: str,
    prior: Optional[PriorTaskContext],
    relation: str,
) -> str:
    """分类/立项用文本：补充/继续时带上原目标，但不无条件污染每条消息。"""
    text = (message or "").strip()
    if not prior or relation not in {TURN_CONTINUE, TURN_SUPPLEMENT}:
        return text
    goal = (prior.goal or "").strip()
    if not goal:
        return text
    return f"{goal}\n用户补充：{text}"


def stage_specs_from_prior(prior: PriorTaskContext):
    """从持久化 stage_plan 恢复 StageSpec 列表；失败则空。"""
    from orchestrator_integration.stage_plan import StageSpec

    specs: List[StageSpec] = []
    for raw in prior.stage_plan or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("stage_id") or "").strip()
        if not sid:
            continue
        specs.append(
            StageSpec(
                stage_id=sid,
                goal=str(raw.get("goal") or "")[:500],
                tool_capabilities=list(raw.get("tool_capabilities") or []),
                depends_on=list(raw.get("depends_on") or []),
                acceptance=str(raw.get("acceptance") or ""),
                auth_actions=list(raw.get("auth_actions") or []),
            )
        )
    return specs


def extract_constraint_patch(message: str) -> Dict[str, str]:
    """从补充话术提取可落库约束（环境/站点等）。"""
    text = message or ""
    patch: Dict[str, str] = {}
    if "测试环境" in text or "用测试" in text:
        patch["environment"] = "test"
    elif "生产环境" in text or "用生产" in text:
        patch["environment"] = "prod"
    elif "预发" in text:
        patch["environment"] = "staging"
    if "美站" in text or "选美" in text:
        patch["site"] = "us"
    elif "欧站" in text or "选欧" in text:
        patch["site"] = "eu"
    elif "中站" in text or "选中" in text:
        patch["site"] = "cn"
    return patch
