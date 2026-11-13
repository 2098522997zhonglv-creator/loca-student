"""证据完整性与进度口径（FR09 / FR10 / 审批恢复闭环）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

_HIST_HTTP_RE = re.compile(
    r"(上一轮|上一次|历史).{0,24}(502|503|500|504|HTTP\s*[45]\d\d)|"
    r"(502|503|500|504).{0,24}(上一轮|上一次|历史)",
    re.I,
)
_PLACEHOLDER_FIELD_RE = re.compile(
    r"(cart_id|cart_item_id|order_sn|quantity|金额|total).{0,12}"
    r"(待补充|TBD|N/A|unknown|占位|假值|示例)",
    re.I,
)
_EQUIV_ENTRY_RE = re.compile(
    r"(等价入口|可用入口|换一个商品|给一个.*(URL|链接|入口)|替代商品)",
    re.I,
)


EVIDENCE_INTEGRITY_HINT = """
# 证据与进度口径（强制）
- 历史 HTTP 5xx/502：只能标「历史异常，尚未复核」；禁止当作本轮仍失败，也禁止据此要求用户先恢复环境。须本轮重新请求/探测后再下结论。
- 历史 cookie / 文件 / session：不等于当前可用。未做本轮探测前，禁止写「cookie 仍在」「完整执行输入已具备」「会话仍存活」。
- 商品能力（普通模式/打印区/定制图等）：仅凭同类商品或知识库推断须标「待验证条件」，不得写成该 PDP 已启用的事实。
- 进度标题：本轮无浏览器/工具业务动作时，用「已整理的输入」，并明确「本轮业务步骤完成数为 0」；禁止用「已完成事项」制造进度错觉。
- 工具不可用或审批恢复后：先报告具体阻塞并继续可执行步骤；禁止堆砌无关知识（support、生产图接口、另一件商品字段）掩盖阻塞。
- 替换入口：须先复核原目标 URL/SKU；确需替换时说明对范围的影响，禁止让用户自行另找目标来承担恢复责任。
- 交付字段：只回报本轮实际捕获的字段与来源；缺失须明示；禁止用占位值补齐 cart_id/数量/金额等。
- 用户已授权上传/加购时：服务端会重挂执行工具；禁止要求用户「放行工具 / 重新授权路由 / 再说继续」才执行。
""".strip()

RESUME_EXEC_HINT = """
# 审批/恢复执行（服务端已闭环）
- 本轮已按未完成任务的原授权重新挂载 execute_skill_script 与相关 Skill；工具可用性问题由服务端处理，不要甩给用户。
- 立即在授权范围内继续（上传/加购等）；仅当本轮探测证明文件不可读、会话死亡或域名不可达时，才报告具体阻塞。
- 禁止输出「请放行工具后再执行」「请重新授权路由」「请再说一次继续」。
""".strip()


def classify_http_failure_freshness(text: str) -> str:
    """返回 current | historical_unverified | none。"""
    t = text or ""
    if not re.search(r"502|503|500|504|HTTP\s*[45]\d\d", t, re.I):
        return "none"
    if _HIST_HTTP_RE.search(t) or "尚未复核" in t:
        return "historical_unverified"
    if any(k in t for k in ("上一轮", "上一次", "历史")):
        return "historical_unverified"
    return "current"


def evaluate_progress_labeling(text: str, *, browser_actions_this_turn: int = 0) -> Dict[str, Any]:
    """检查进度措辞是否制造错觉。"""
    t = text or ""
    ok = True
    reasons: List[str] = []
    if browser_actions_this_turn <= 0:
        if re.search(r"已完成事项|本轮已完成|已完成：", t) and not re.search(
            r"已整理的输入|业务步骤完成数为\s*0", t
        ):
            ok = False
            reasons.append("无本轮浏览器动作时不得用「已完成事项」；应标已整理的输入且完成数为 0")
    return {"ok": ok, "reasons": reasons}


def evaluate_evidence_integrity_text(
    text: str,
    *,
    probe_ok_to_reuse: Optional[bool] = None,
    browser_actions_this_turn: int = 0,
) -> Dict[str, Any]:
    """对模型答复/工具叙述做证据完整性启发式检查。"""
    t = text or ""
    ok = True
    reasons: List[str] = []
    freshness = classify_http_failure_freshness(t)
    if freshness == "historical_unverified":
        if re.search(r"(请|麻烦).{0,12}(恢复|重启|排查).{0,12}(服务|环境|网关)", t):
            ok = False
            reasons.append("历史 502/5xx 不得当作本轮故障去要求环境恢复")
        if "尚未复核" not in t and "历史异常" not in t:
            reasons.append("历史 HTTP 异常应标为「历史异常，尚未复核」")
            # 措辞缺失视为软失败（仍记 reasons）
            ok = False

    if probe_ok_to_reuse is False or probe_ok_to_reuse is None:
        if re.search(r"cookie\s*仍在|会话仍(存活|有效)|完整执行输入已具备", t, re.I):
            if probe_ok_to_reuse is not True:
                ok = False
                reasons.append("未通过本轮探测前禁止宣称 cookie/会话/输入已可用")

    if re.search(r"(普通模式|打印区域|定制图).{0,16}(已启用|支持|可用)", t) and (
        "待验证" not in t and "本商品" not in t and "实际配置" not in t
    ):
        if re.search(r"手提袋|同类|这类商品|知识库", t):
            ok = False
            reasons.append("商品能力推断须标待验证，不能直接当成本 PDP 事实")

    prog = evaluate_progress_labeling(
        t, browser_actions_this_turn=browser_actions_this_turn
    )
    if not prog["ok"]:
        ok = False
        reasons.extend(prog["reasons"])

    if _EQUIV_ENTRY_RE.search(t) and "原目标" not in t and "影响" not in t:
        ok = False
        reasons.append("提出替换入口前须复核原目标并说明影响")

    if _PLACEHOLDER_FIELD_RE.search(t):
        ok = False
        reasons.append("禁止用占位值补齐交付字段")

    return {
        "ok": ok,
        "reasons": reasons,
        "http_freshness": freshness,
    }


def count_browser_actions(tool_results: Sequence[str]) -> int:
    joined = "\n".join(str(x) for x in tool_results if x)
    if not joined.strip():
        return 0
    hits = 0
    for pat in (
        r"cart_id\s*[:=]",
        r"add[_ ]?to[_ ]?cart",
        r"setInputFiles|filechooser|upload",
        r"page\.(goto|click|fill)",
        r"preview",
    ):
        if re.search(pat, joined, re.I):
            hits += 1
    return hits
