"""复合任务阶段计划（FR08）。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from orchestrator_integration.intent_router import (
    INTENT_BROWSER,
    INTENT_PLATFORM,
    INTENT_SEARCH,
    INTENT_WEB,
    INTENT_WRITE,
    IntentDecision,
)

_COMPOSITE_MARKERS = (
    "生成用例",
    "保存",
    "落库",
    "执行用例",
    "执行测试",
    "再汇总",
    "然后执行",
    "保存后执行",
    "查规则",
    "根据知识库",
)


@dataclass
class StageSpec:
    stage_id: str
    goal: str
    tool_capabilities: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    acceptance: str = ""
    auth_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_composite_request(message: str, decision: Optional[IntentDecision] = None) -> bool:
    text = message or ""
    hits = sum(1 for m in _COMPOSITE_MARKERS if m in text)
    if hits >= 2:
        return True
    if decision and INTENT_WRITE in decision.intents and INTENT_SEARCH in decision.intents:
        return True
    if decision and INTENT_WRITE in decision.intents and "执行" in text:
        return True
    return False


def build_stage_plan(
    message: str,
    decision: IntentDecision,
    *,
    playbook_id: str = "",
) -> List[StageSpec]:
    """生成有序阶段；单阶段请求返回单元素计划。"""
    text = message or ""
    if playbook_id == "browser_act" or INTENT_BROWSER in decision.intents:
        return [
            StageSpec(
                stage_id="browser_interact",
                goal="在授权范围内完成页面交互并交付真实标识",
                tool_capabilities=["playwright-skill", "read_skill_content", "execute_skill_script"],
                acceptance="上传/加购检查点通过且标识类型明确",
                auth_actions=["upload", "add_to_cart"],
            )
        ]
    if playbook_id == "testcase_execute":
        return [
            StageSpec(
                stage_id="execute_testcase",
                goal="执行测试用例并回报终态",
                tool_capabilities=["ui-automation", "loca-stude", "execute_skill_script"],
                acceptance="执行记录终态可查",
                auth_actions=["execute_testcase"],
            )
        ]
    if not is_composite_request(text, decision):
        return [
            StageSpec(
                stage_id="single",
                goal=text[:200] or "完成本轮用户目标",
                tool_capabilities=[],
                acceptance="完成用户目标且无越权",
            )
        ]

    stages: List[StageSpec] = []
    if any(k in text for k in ("知识库", "查规则", "根据文档", "检索")) or INTENT_SEARCH in decision.intents:
        stages.append(
            StageSpec(
                stage_id="kb_research",
                goal="检索规则并记录来源",
                tool_capabilities=["knowledge_search"],
                acceptance="有效内容与来源",
                auth_actions=["knowledge_search"],
            )
        )
    if any(k in text for k in ("生成", "草稿", "用例")):
        stages.append(
            StageSpec(
                stage_id="generate_draft",
                goal="生成结构化草稿",
                tool_capabilities=["read_skill_content"],
                depends_on=[s.stage_id for s in stages[-1:]] if stages else [],
                acceptance="草稿字段完整",
                auth_actions=["generate"],
            )
        )
    if any(k in text for k in ("保存", "落库", "写入", "创建")) or INTENT_WRITE in decision.intents:
        stages.append(
            StageSpec(
                stage_id="persist",
                goal="按授权保存并回读",
                tool_capabilities=["loca-stude", "execute_skill_script"],
                depends_on=[s.stage_id for s in stages[-1:]] if stages else [],
                acceptance="实体 ID 回读成功",
                auth_actions=["write"],
            )
        )
    if any(k in text for k in ("执行用例", "执行测试", "跑一遍", "执行")):
        stages.append(
            StageSpec(
                stage_id="execute",
                goal="用产物 ID 启动执行并查终态",
                tool_capabilities=["ui-automation", "execute_skill_script"],
                depends_on=[s.stage_id for s in stages if s.stage_id == "persist"]
                or ([s.stage_id for s in stages[-1:]] if stages else []),
                acceptance="执行记录终态",
                auth_actions=["execute_testcase"],
            )
        )
    stages.append(
        StageSpec(
            stage_id="summarize",
            goal="验收并汇总来源与未完成项",
            tool_capabilities=[],
            depends_on=[s.stage_id for s in stages[-1:]] if stages else [],
            acceptance="最终答复含产物与验收状态",
            auth_actions=[],
        )
    )
    return stages


_BUILTIN_CAPS = frozenset(
    {
        "knowledge_search",
        "read_skill_content",
        "execute_skill_script",
        "generate",
        "request_tool_expansion",
        "assert_task_postcondition",
        "report_task_blocked",
    }
)
_META_ALWAYS = frozenset(
    {
        "request_tool_expansion",
        "assert_task_postcondition",
        "report_task_blocked",
        "read_skill_content",
        "execute_skill_script",
    }
)


def stage_tool_allowlist(stages: Sequence[StageSpec], current_stage_id: str) -> Optional[set]:
    """当前阶段可挂载 Skill 名；None 表示不额外限制。"""
    policy = stage_mount_policy(stages, current_stage_id)
    if not policy.get("restrict"):
        return None
    skills = set(policy.get("skill_names") or [])
    return skills or set()


def stage_mount_policy(
    stages: Sequence[StageSpec], current_stage_id: str
) -> Dict[str, Any]:
    """阶段挂载策略：Skill 白名单 + 内置工具名。

    restrict=False 表示单阶段/空能力，不额外裁剪。
    """
    by_id = {s.stage_id: s for s in stages}
    cur = by_id.get(current_stage_id)
    if not cur:
        return {"restrict": False, "skill_names": set(), "builtin_tools": set()}
    caps = set(cur.tool_capabilities or [])
    for dep in cur.depends_on or []:
        prev = by_id.get(dep)
        if prev:
            caps.update(prev.tool_capabilities or [])
    if not caps and cur.stage_id in {"single", "summarize"}:
        return {"restrict": False, "skill_names": set(), "builtin_tools": set()}
    skills = {c for c in caps if c not in _BUILTIN_CAPS}
    builtins = {c for c in caps if c in _BUILTIN_CAPS} | set(_META_ALWAYS)
    # 有 Skill 能力时默认挂载读写脚本工具
    if skills:
        builtins |= {"read_skill_content", "execute_skill_script"}
    return {
        "restrict": True,
        "skill_names": skills,
        "builtin_tools": builtins,
        "stage_id": cur.stage_id,
        "acceptance": cur.acceptance,
    }


def format_stage_plan_hint(
    stages: Sequence[StageSpec], current_stage_id: str = ""
) -> str:
    if not stages:
        return ""
    cur = current_stage_id or (stages[0].stage_id if stages else "")
    lines = ["# 阶段计划（服务端）"]
    for i, s in enumerate(stages, 1):
        mark = "→" if s.stage_id == cur else "·"
        deps = f" depends_on={','.join(s.depends_on)}" if s.depends_on else ""
        lines.append(
            f"{mark} [{i}/{len(stages)}] {s.stage_id}: {s.goal}"
            f"{deps} | 验收: {s.acceptance or '完成目标'}"
        )
    lines.append(
        "- 当前阶段未完成前不要跳到后续写入/执行；依赖失败则跳过依赖方并说明。"
    )
    return "\n".join(lines)


def advance_stage(
    stages: Sequence[StageSpec], current_stage_id: str, *, accepted: bool
) -> str:
    """验收通过后推进到下一阶段；失败则停在当前。"""
    if not stages or not accepted:
        return current_stage_id or ""
    ids = [s.stage_id for s in stages]
    if current_stage_id not in ids:
        return ids[0]
    idx = ids.index(current_stage_id)
    if idx + 1 < len(ids):
        return ids[idx + 1]
    return current_stage_id


def infer_auth_scope(message: str) -> Dict[str, Any]:
    """从用户原话推断授权/禁止动作（FR05.5）。"""
    text = message or ""
    allow = []
    deny = []
    if any(k in text for k in ("加购", "加入购物车", "Add To Cart", "add to cart")):
        allow.append("add_to_cart")
    if any(k in text for k in ("上传", "传图", "Upload Photo", "upload")):
        allow.append("upload")
    if any(k in text for k in ("提交订单", "下单", "结算")):
        allow.append("submit_order")
    if any(k in text for k in ("支付", "付款")) and not any(
        k in text for k in ("不要支付", "不需要支付", "不支付", "禁止支付")
    ):
        allow.append("pay")
    if any(k in text for k in ("不要支付", "不需要支付", "不支付", "禁止支付")):
        deny.append("pay")
    # 只说加购/不要支付 → 默认不授权 submit_order
    if "add_to_cart" in allow and "submit_order" not in allow:
        deny.append("submit_order")
    deliver = []
    if any(k in text for k in ("订单号", "order_sn", "oid")):
        deliver.append("order_sn")
    if any(k in text for k in ("购物车", "cart", "加购")):
        deliver.append("cart_id")
    method = ""
    if any(k in text for k in ("真实文件选择", "原生", "filechooser", "文件选择器")):
        method = "native_or_filechooser"
    return {
        "allow_actions": sorted(set(allow)),
        "deny_actions": sorted(set(deny)),
        "deliver_identifiers": sorted(set(deliver)),
        "upload_method_requirement": method,
        "raw_goal": text[:2000],
    }
