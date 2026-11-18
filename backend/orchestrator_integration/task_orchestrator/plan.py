"""FR03/FR05：结构化任务计划与事实契约。"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from orchestrator_integration.intent_router import (
    INTENT_BROWSER,
    INTENT_PLATFORM,
    INTENT_SEARCH,
    INTENT_WRITE,
    IntentDecision,
)
from orchestrator_integration.stage_plan import infer_auth_scope
from orchestrator_integration.task_orchestrator.capability_catalog import (
    capability_exists,
)
from orchestrator_integration.task_orchestrator.flag import STRATEGY_VERSION

NODE_PENDING = "pending"
NODE_RUNNING = "running"
NODE_DONE = "done"
NODE_WAITING_USER = "waiting_user"
NODE_FAILED = "failed"

TASK_RUNNING = "running"
TASK_WAITING_USER = "waiting_user"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanFact:
    key: str
    value: Any
    source: str = ""
    confirmed_at: str = ""
    subject: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PendingAuthAction:
    action_id: str
    task_id: str
    node_id: str
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    impact_summary: str = ""
    approval: str = "pending"  # pending | approved | denied

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanNode:
    node_id: str
    goal: str
    input_refs: List[str] = field(default_factory=list)
    output_keys: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    primary_candidates: List[str] = field(default_factory=list)
    fallback_candidates: List[str] = field(default_factory=list)
    pending_auth_actions: List[str] = field(default_factory=list)
    completion_checks: List[str] = field(default_factory=list)
    status: str = NODE_PENDING

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    task_id: str
    goal: str
    deliverables: List[str] = field(default_factory=list)
    authorization_scope: Dict[str, Any] = field(default_factory=dict)
    forbidden_actions: List[str] = field(default_factory=list)
    facts: Dict[str, PlanFact] = field(default_factory=dict)
    nodes: List[PlanNode] = field(default_factory=list)
    pending_auth: List[PendingAuthAction] = field(default_factory=list)
    estimated_nodes: int = 0
    current_node: str = ""
    status: str = TASK_RUNNING
    strategy_version: str = STRATEGY_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "deliverables": list(self.deliverables),
            "authorization_scope": dict(self.authorization_scope),
            "forbidden_actions": list(self.forbidden_actions),
            "facts": {k: v.to_dict() for k, v in self.facts.items()},
            "nodes": [n.to_dict() for n in self.nodes],
            "pending_auth": [p.to_dict() for p in self.pending_auth],
            "estimated_nodes": self.estimated_nodes,
            "current_node": self.current_node,
            "status": self.status,
            "strategy_version": self.strategy_version,
        }

    def node_by_id(self, node_id: str) -> Optional[PlanNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def current(self) -> Optional[PlanNode]:
        return self.node_by_id(self.current_node) if self.current_node else (
            self.nodes[0] if self.nodes else None
        )


def plan_from_dict(data: Optional[Dict[str, Any]]) -> Optional[TaskPlan]:
    if not isinstance(data, dict) or not data.get("task_id"):
        return None
    facts: Dict[str, PlanFact] = {}
    for k, v in (data.get("facts") or {}).items():
        if isinstance(v, dict):
            facts[k] = PlanFact(
                key=v.get("key") or k,
                value=v.get("value"),
                source=str(v.get("source") or ""),
                confirmed_at=str(v.get("confirmed_at") or ""),
                subject=str(v.get("subject") or ""),
            )
    nodes = []
    for raw in data.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nodes.append(
            PlanNode(
                node_id=str(raw.get("node_id") or ""),
                goal=str(raw.get("goal") or ""),
                input_refs=list(raw.get("input_refs") or []),
                output_keys=list(raw.get("output_keys") or []),
                depends_on=list(raw.get("depends_on") or []),
                primary_candidates=list(raw.get("primary_candidates") or []),
                fallback_candidates=list(raw.get("fallback_candidates") or []),
                pending_auth_actions=list(raw.get("pending_auth_actions") or []),
                completion_checks=list(raw.get("completion_checks") or []),
                status=str(raw.get("status") or NODE_PENDING),
            )
        )
    pending = []
    for raw in data.get("pending_auth") or []:
        if not isinstance(raw, dict):
            continue
        pending.append(
            PendingAuthAction(
                action_id=str(raw.get("action_id") or ""),
                task_id=str(raw.get("task_id") or ""),
                node_id=str(raw.get("node_id") or ""),
                target=str(raw.get("target") or ""),
                params=dict(raw.get("params") or {}),
                impact_summary=str(raw.get("impact_summary") or ""),
                approval=str(raw.get("approval") or "pending"),
            )
        )
    return TaskPlan(
        task_id=str(data["task_id"]),
        goal=str(data.get("goal") or ""),
        deliverables=list(data.get("deliverables") or []),
        authorization_scope=dict(data.get("authorization_scope") or {}),
        forbidden_actions=list(data.get("forbidden_actions") or []),
        facts=facts,
        nodes=nodes,
        pending_auth=pending,
        estimated_nodes=int(data.get("estimated_nodes") or len(nodes)),
        current_node=str(data.get("current_node") or (nodes[0].node_id if nodes else "")),
        status=str(data.get("status") or TASK_RUNNING),
        strategy_version=str(data.get("strategy_version") or STRATEGY_VERSION),
    )


def _filter_real(caps: Sequence[str]) -> List[str]:
    out: List[str] = []
    for c in caps:
        cid = (c or "").strip()
        if not cid:
            continue
        if capability_exists(cid) or cid.startswith("mcp:"):
            out.append(cid)
        # 规划阶段允许 skill 名即使 catalog 瞬时缺失（本地未 reload）
        elif re.match(r"^[a-z0-9][a-z0-9_\-]+$", cid):
            out.append(cid)
    # 去重保序
    seen = set()
    uniq = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def build_task_plan(
    message: str,
    decision: IntentDecision,
    *,
    task_id: Optional[str] = None,
    prior_facts: Optional[Dict[str, Any]] = None,
    auth_scope: Optional[Dict[str, Any]] = None,
) -> TaskPlan:
    """生成可推进的结构化计划；简单任务单节点。"""
    text = (message or "").strip()
    tid = (task_id or "").strip() or f"orch-{uuid.uuid4().hex[:12]}"
    scope = dict(auth_scope or infer_auth_scope(text))
    allow = set(scope.get("allow_actions") or [])
    deny = set(scope.get("deny_actions") or [])
    deliver = list(scope.get("deliver_identifiers") or [])

    facts: Dict[str, PlanFact] = {}
    if isinstance(prior_facts, dict):
        for k, v in prior_facts.items():
            if k in {"strategy_version", "task_plan", "ready_stages"}:
                continue
            if isinstance(v, dict) and "value" in v:
                facts[k] = PlanFact(
                    key=k,
                    value=v.get("value"),
                    source=str(v.get("source") or "prior"),
                    confirmed_at=str(v.get("confirmed_at") or _now_iso()),
                    subject=str(v.get("subject") or ""),
                )
            else:
                facts[k] = PlanFact(
                    key=k, value=v, source="prior", confirmed_at=_now_iso()
                )

    nodes = _build_nodes(text, decision, allow=allow, deny=deny, facts=facts)
    # 校验并清洗候选能力标识
    for n in nodes:
        n.primary_candidates = _filter_real(n.primary_candidates)
        n.fallback_candidates = _filter_real(n.fallback_candidates)

    pending: List[PendingAuthAction] = []
    for n in nodes:
        for action in n.pending_auth_actions:
            pending.append(
                PendingAuthAction(
                    action_id=f"{n.node_id}:{action}",
                    task_id=tid,
                    node_id=n.node_id,
                    target=action,
                    impact_summary=f"节点「{n.goal}」需要动作 {action}",
                )
            )

    current = nodes[0].node_id if nodes else ""
    # 跳过条件已满足的前缀节点
    known_entry = any(
        k in facts for k in ("entry_url", "site_url", "pdp_url", "base_url")
    )
    if known_entry and nodes and nodes[0].node_id == "fetch_conditions":
        nodes[0].status = NODE_DONE
        current = nodes[1].node_id if len(nodes) > 1 else nodes[0].node_id

    return TaskPlan(
        task_id=tid,
        goal=text[:2000] or "完成本轮用户目标",
        deliverables=deliver
        or (
            ["order_sn"]
            if "submit_order" in allow
            else (["cart_id"] if "add_to_cart" in allow else [])
        ),
        authorization_scope=scope,
        forbidden_actions=sorted(deny),
        facts=facts,
        nodes=nodes,
        pending_auth=pending,
        estimated_nodes=len(nodes),
        current_node=current,
        status=TASK_RUNNING,
        strategy_version=STRATEGY_VERSION,
    )


def _build_nodes(
    text: str,
    decision: IntentDecision,
    *,
    allow: set,
    deny: set,
    facts: Dict[str, PlanFact],
) -> List[PlanNode]:
    browserish = INTENT_BROWSER in decision.intents or any(
        k in text
        for k in (
            "浏览器",
            "加购",
            "上传",
            "下单",
            "所见即所得",
            "所见所得",
            "callie",
            "playwright",
            "购物车",
        )
    )
    executeish = any(
        k in text for k in ("执行用例", "执行测试", "跑用例", "testcase", "自动化执行")
    )
    searchish = INTENT_SEARCH in decision.intents or any(
        k in text for k in ("知识库", "查规则", "入口", "分支", "tip-")
    )
    platform_write = INTENT_WRITE in decision.intents and INTENT_PLATFORM in decision.intents

    # 用例执行路径：查询 → 执行/浏览器兜底 → 交付
    if executeish and not browserish:
        return [
            PlanNode(
                node_id="query_cases",
                goal="查询可执行 UI/功能用例与环境配置",
                primary_candidates=["ui-automation", "loca-stude", "execute_skill_script"],
                fallback_candidates=["knowledge_search", "read_skill_content"],
                output_keys=["testcase_id", "env_base_url"],
                completion_checks=["已确认有无 UiTestCase 或功能用例步骤"],
            ),
            PlanNode(
                node_id="run_or_browser",
                goal="有 UI 用例则执行；否则浏览器按步骤实测",
                depends_on=["query_cases"],
                primary_candidates=[
                    "ui-automation",
                    "playwright-skill",
                    "execute_skill_script",
                ],
                fallback_candidates=["playwright-cli", "request_executor_heal"],
                output_keys=["execution_result", "screenshots"],
                completion_checks=["返回真实执行结果或页面观察证据"],
            ),
            PlanNode(
                node_id="deliver",
                goal="汇总通过/失败与证据",
                depends_on=["run_or_browser"],
                primary_candidates=["assert_task_postcondition"],
                fallback_candidates=["report_task_blocked"],
                output_keys=["summary"],
                completion_checks=["交付状态与证据，无伪造成功"],
            ),
        ]

    # 浏览器下单参考路径（需求 §5）
    if browserish:
        nodes: List[PlanNode] = []
        if searchish or "entry_url" not in facts:
            nodes.append(
                PlanNode(
                    node_id="fetch_conditions",
                    goal="获取站点入口、目标商品与必要规则",
                    primary_candidates=["knowledge_search", "read_skill_content"],
                    fallback_candidates=["loca-stude", "url-reader"],
                    output_keys=["entry_url", "product_hint", "rules"],
                    completion_checks=["入口或推导规则已具备"],
                )
            )
        dep0 = [nodes[0].node_id] if nodes else []
        nodes.append(
            PlanNode(
                node_id="check_executable",
                goal="检查入口可访问且商品条件可操作",
                depends_on=list(dep0),
                primary_candidates=["playwright-skill", "execute_skill_script"],
                fallback_candidates=["knowledge_search", "playwright-cli"],
                input_refs=["entry_url"],
                output_keys=["page_ok", "product_url"],
                completion_checks=["页面可访问；目标商品可定位"],
            )
        )
        if "upload" in allow or any(
            k in text for k in ("上传", "传图", "所见即所得", "所见所得")
        ):
            nodes.append(
                PlanNode(
                    node_id="customize_upload",
                    goal="定制并上传测试图，核验预览",
                    depends_on=["check_executable"],
                    primary_candidates=["playwright-skill", "execute_skill_script"],
                    fallback_candidates=["playwright-cli", "request_executor_heal"],
                    input_refs=["product_url", "test_image"],
                    output_keys=["upload_ok", "preview_ok"],
                    completion_checks=["上传成功状态；预览与测试图对应"],
                )
            )
            cart_dep = ["customize_upload"]
        else:
            cart_dep = ["check_executable"]

        if "add_to_cart" in allow or any(
            k in text for k in ("加购", "购物车", "下单", "订单")
        ):
            nodes.append(
                PlanNode(
                    node_id="add_to_cart",
                    goal="加入购物车并校验真实返回",
                    depends_on=list(cart_dep),
                    primary_candidates=["playwright-skill", "execute_skill_script"],
                    fallback_candidates=["playwright-cli", "request_executor_heal"],
                    output_keys=["cart_id", "cart_item_id", "amount"],
                    completion_checks=["站点真实购物车标识；不编造 cart_id"],
                )
            )
            order_dep = ["add_to_cart"]
        else:
            order_dep = list(cart_dep)

        if "submit_order" in allow:
            nodes.append(
                PlanNode(
                    node_id="submit_order",
                    goal="提交测试订单（不支付）并取得订单标识",
                    depends_on=list(order_dep),
                    primary_candidates=["playwright-skill", "execute_skill_script"],
                    fallback_candidates=["request_executor_heal"],
                    output_keys=["order_sn"],
                    completion_checks=["真实 order_sn；未执行支付"],
                )
            )
            deliver_dep = ["submit_order"]
        elif "submit_order" not in deny and any(
            k in text for k in ("下单", "提交订单", "订单号")
        ):
            # 用户口头要订单但授权推断未放行 → 三层待授权
            nodes.append(
                PlanNode(
                    node_id="submit_order",
                    goal="提交测试订单（待用户授权）",
                    depends_on=list(order_dep),
                    primary_candidates=[],
                    fallback_candidates=[],
                    pending_auth_actions=["submit_order"],
                    output_keys=["order_sn"],
                    completion_checks=["取得批准后提交并返回 order_sn"],
                )
            )
            deliver_dep = ["submit_order"]
        else:
            deliver_dep = list(order_dep)

        nodes.append(
            PlanNode(
                node_id="verify_deliver",
                goal="核验业务终态并交付约定字段",
                depends_on=list(deliver_dep),
                primary_candidates=["playwright-skill", "assert_task_postcondition"],
                fallback_candidates=["knowledge_search", "report_task_blocked"],
                output_keys=["deliverables"],
                completion_checks=["目标对象、状态、约定字段与证据齐全"],
            )
        )
        return nodes

    if platform_write:
        return [
            PlanNode(
                node_id="platform_draft",
                goal="只读确认后准备平台写入草稿",
                primary_candidates=["loca-stude", "execute_skill_script", "read_skill_content"],
                fallback_candidates=["knowledge_search"],
                pending_auth_actions=["platform_write"] if "确认" not in text else [],
                output_keys=["draft"],
                completion_checks=["写入前已确认或等待批准"],
            )
        ]

    # 简单任务：单节点
    caps = ["knowledge_search"] if searchish else []
    if INTENT_PLATFORM in decision.intents:
        caps = ["loca-stude", "execute_skill_script", "read_skill_content"]
    return [
        PlanNode(
            node_id="single",
            goal=text[:200] or "完成本轮用户目标",
            primary_candidates=_filter_real(caps) or ["knowledge_search"],
            fallback_candidates=["read_skill_content", "report_task_blocked"],
            completion_checks=["完成用户目标且无越权"],
        )
    ]


def revise_affected_nodes(
    plan: TaskPlan,
    *,
    conflict_keys: Optional[Sequence[str]] = None,
    entry_changed: bool = False,
) -> TaskPlan:
    """依赖不成立或入口变化时仅修订受影响节点。"""
    touch = set(conflict_keys or [])
    if entry_changed:
        touch |= {"entry_url", "product_url", "pdp_url"}
    if not touch:
        return plan
    for n in plan.nodes:
        if n.status == NODE_DONE and (
            touch.intersection(n.output_keys) or touch.intersection(n.input_refs)
        ):
            n.status = NODE_PENDING
        if any(d for d in n.depends_on if plan.node_by_id(d) and plan.node_by_id(d).status != NODE_DONE):
            if n.status == NODE_DONE:
                n.status = NODE_PENDING
    # 当前节点回退到第一个未完成
    for n in plan.nodes:
        if n.status != NODE_DONE:
            plan.current_node = n.node_id
            break
    return plan
