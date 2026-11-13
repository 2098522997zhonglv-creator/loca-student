"""证据完整性与进度口径（FR09 / FR10 / 审批恢复闭环）。

设计原则：先执行、后标注。长清单「强制」提示会压制工具调用，只在浏览器/
加购等执行面注入短提示；交付门禁只拦硬伤，措辞类问题记 soft reasons。
"""
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
_ENV_RECOVERY_RE = re.compile(
    r"(请|麻烦).{0,12}(恢复|重启|排查).{0,12}(服务|环境|网关)",
    re.I,
)


# 短提示：强调继续执行，避免模型陷入「先写合规报告」
EVIDENCE_INTEGRITY_HINT = """
# 证据口径（执行优先）
- 先在授权范围内继续工具执行；不要为了合规措辞停手或把恢复责任甩给用户。
- 历史 5xx/502：标「历史异常，尚未复核」，须本轮重试/探测后再下结论；禁止据此要求用户先恢复环境。
- 未做本轮探测前，勿写「cookie 仍在 / 输入已具备」；交付只报本轮实际捕获字段，缺则明示，禁用占位值。
""".strip()

RESUME_EXEC_HINT = """
# 审批/恢复执行（服务端已闭环）
- 已按原授权重挂 execute_skill_script；立即继续上传/加购，不要让用户「放行工具 / 再说继续」。
- 仅当本轮探测证明文件不可读、会话死亡或域名不可达时，才报告具体阻塞。
""".strip()


def should_inject_evidence_hint(
    *,
    preserve_exec: bool = False,
    allow_execute: bool = False,
    zone_id: str = "",
    allow_actions: Optional[Sequence[str]] = None,
) -> bool:
    """仅浏览器/执行保留轮注入，避免污染纯问答。"""
    if preserve_exec:
        return True
    z = (zone_id or "").lower()
    if z in {"browser", "web", "execute"}:
        return True
    actions = set(allow_actions or [])
    if actions & {"upload", "add_to_cart", "browser", "checkout"}:
        return True
    return bool(allow_execute and actions)


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


def evaluate_progress_labeling(
    text: str, *, browser_actions_this_turn: int = 0
) -> Dict[str, Any]:
    """检查进度措辞（soft，不单独否决交付）。"""
    t = text or ""
    ok = True
    reasons: List[str] = []
    if browser_actions_this_turn <= 0:
        if re.search(r"已完成事项|本轮已完成|已完成：", t) and not re.search(
            r"已整理的输入|业务步骤完成数为\s*0", t
        ):
            ok = False
            reasons.append(
                "无本轮浏览器动作时宜用「已整理的输入」并标明完成数为 0"
            )
    return {"ok": ok, "reasons": reasons}


def evaluate_evidence_integrity_text(
    text: str,
    *,
    probe_ok_to_reuse: Optional[bool] = None,
    browser_actions_this_turn: int = 0,
) -> Dict[str, Any]:
    """证据完整性检查。

    hard_ok：交付门禁只看此项（甩锅恢复环境 / 占位字段 / 未探测却宣称可用）。
    soft reasons：措辞与推断提醒，不否决任务完成。
    """
    t = text or ""
    hard_ok = True
    soft_ok = True
    hard_reasons: List[str] = []
    soft_reasons: List[str] = []
    freshness = classify_http_failure_freshness(t)

    if freshness == "historical_unverified":
        if _ENV_RECOVERY_RE.search(t):
            hard_ok = False
            hard_reasons.append("历史 502/5xx 不得当作本轮故障去要求环境恢复")
        if "尚未复核" not in t and "历史异常" not in t:
            soft_ok = False
            soft_reasons.append("历史 HTTP 异常宜标为「历史异常，尚未复核」")

    if probe_ok_to_reuse is not True:
        if re.search(r"cookie\s*仍在|会话仍(存活|有效)|完整执行输入已具备", t, re.I):
            hard_ok = False
            hard_reasons.append("未通过本轮探测前禁止宣称 cookie/会话/输入已可用")

    capability_claim = bool(
        re.search(
            r"(已启用|支持|可用).{0,24}(普通模式|打印区域|定制图)|"
            r"(普通模式|打印区域|定制图).{0,16}(已启用|支持|可用)",
            t,
        )
    )
    if capability_claim and (
        "待验证" not in t and "本商品" not in t and "实际配置" not in t
    ):
        if re.search(r"手提袋|同类|这类商品|知识库|类目经验", t):
            # RC17：若据此宣称本轮可执行/已完成 → 硬拦；仅推断 → soft
            if re.search(
                r"(因此|故).{0,16}(可直接|已经|已完成|加购成功|上传成功)|"
                r"(可直接上传|并加购成功)",
                t,
            ):
                hard_ok = False
                hard_reasons.append("类目经验不得当作目标 SKU 能力或完成证据")
            else:
                soft_ok = False
                soft_reasons.append("商品能力推断宜标待验证，勿直接当成本 PDP 事实")

    prog = evaluate_progress_labeling(
        t, browser_actions_this_turn=browser_actions_this_turn
    )
    if not prog["ok"]:
        soft_ok = False
        soft_reasons.extend(prog["reasons"])

    if _EQUIV_ENTRY_RE.search(t) and "原目标" not in t and "影响" not in t:
        soft_ok = False
        soft_reasons.append("提出替换入口前宜复核原目标并说明影响")

    if _PLACEHOLDER_FIELD_RE.search(t):
        hard_ok = False
        hard_reasons.append("禁止用占位值补齐交付字段")

    return {
        "ok": hard_ok,  # 兼容旧调用：ok = 硬门禁
        "hard_ok": hard_ok,
        "soft_ok": soft_ok,
        "reasons": hard_reasons,  # 兼容：reasons = 硬原因
        "hard_reasons": hard_reasons,
        "soft_reasons": soft_reasons,
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
