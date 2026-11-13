"""执行策略：预算归因、批次顺序、完成验证与失败分类（FR01–FR03 / FR02）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestrator_integration.skill_catalog import (
    extract_command_actions,
    resolve_action_side_effect,
)
from orchestrator_integration.task_commission import CHANNEL_HEAVY, CHANNEL_LIGHT

_ATTR_PLANNING = "planning"
_ATTR_TOOL = "tool"
_ATTR_BUSINESS = "business"
_ATTR_CONCURRENCY = "concurrency"

_BUSINESS_RE = re.compile(
    r"余额不足|库存不足|权限不足|无权限|已存在|不存在|状态不允许|业务校验|business[_ ]?error|rejected by policy",
    re.IGNORECASE,
)
_TOOL_RE = re.compile(
    r"timeout|timed out|连接失败|ConnectionError|HTTP\s*[45]\d\d|500|502|503|traceback|Exception|Errno",
    re.IGNORECASE,
)
_CONCURRENCY_RE = re.compile(
    r"ETag|version conflict|乐观锁|并发冲突|stale|被他人修改|409\b",
    re.IGNORECASE,
)
_PLANNING_RE = re.compile(
    r"不在本轮|白名单|辅类|越权|Escape Hatch|亲和组|未登记|needs_confirmation|禁止",
    re.IGNORECASE,
)
_BROWSER_TIMEOUT_RE = re.compile(
    r"timeout|timed out|超时|filechooser|等待.*超时",
    re.IGNORECASE,
)
_BROWSER_SUCCESS_MARKERS = re.compile(
    r"(?<!failed\s)(?<!failing\s)add\s*to\s*cart(?!\s*failed)|"
    r"加入购物车(?!失败)|"
    r"cart_id\s*[:=]\s*(?!unknown\b|null\b|none\b)\S+|"
    r"cart_item_id\s*[:=]\s*(?!unknown\b|null\b|none\b)\S+|"
    r"/cart/|"
    r"已加入购物车|"
    r"1/1|"
    r"preview\s*(refresh|更新|已刷新)|预览刷新|"
    r"order_sn\s*[:=]\s*(?!unknown\b|null\b|none\b)\S+|"
    r"订单号\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_TYPED_ID_RE = re.compile(
    r"(cart_id|cart_item_id|checkout_id|order_id|order_sn)\s*[:=]\s*"
    r"(?!unknown\b|null\b|none\b|n/?a\b|undefined\b)([A-Za-z0-9][A-Za-z0-9_-]{2,})",
    re.IGNORECASE,
)
# D03/RC18：点击动作观察值 / 静态 Preview 文案不得冒充类型化编号
_PLACEHOLDER_ID_VALUES = frozenset(
    {
        "observed_cart_action",
        "observed_cart",
        "placeholder",
        "static_preview",
        "preview",
        "tbd",
        "todo",
        "sample",
        "demo",
        "example",
    }
)
_STATIC_PREVIEW_ONLY_RE = re.compile(
    r"(?i)(static\s*preview|预览文案|preview\s*text\s*only|"
    r"仅有\s*preview\s*文本|页面含\s*preview\s*字样)"
)
_UPLOAD_COUNT_ONLY_RE = re.compile(r"0/1\s*[→\-~]+\s*1/1|计数.*1/1", re.IGNORECASE)
_NEGATED_SUCCESS_RE = re.compile(
    r"(add\s*to\s*cart|加入购物车|上传|upload|preview|预览).{0,12}"
    r"(failed|fail|失败|未|不|超时|timeout)|"
    r"(failed|fail|失败|超时|timeout|未刷新|not\s+refresh).{0,16}"
    r"(add\s*to\s*cart|加入购物车|上传|preview|预览)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BatchPlan:
    ordered_commands: List[dict]
    parallel: bool
    notes: Tuple[str, ...]


def classify_attribution(
    *,
    error_kind: str = "",
    result_text: str = "",
    http_ok: Optional[bool] = None,
) -> str:
    """偏差归因四象限：planning / tool / business / concurrency。"""
    blob = f"{error_kind}\n{result_text or ''}"
    if error_kind in {
        "allowlist",
        "cross_skill",
        "privilege",
        "unregistered_write",
        "escape_denied",
        "budget_exceeded",
        "batch_plan_reject",
        "needs_confirmation",
        "needs_confirmation_partial",
        "no_commission",
        "postcondition_failed",
        "light_channel_irreversible",
        "nonzero_exit",
        "timeout_unknown",
    }:
        return _ATTR_PLANNING if error_kind not in {
            "nonzero_exit",
            "timeout_unknown",
        } else _ATTR_TOOL
    if _PLANNING_RE.search(blob):
        return _ATTR_PLANNING
    if _CONCURRENCY_RE.search(blob):
        return _ATTR_CONCURRENCY
    if _BUSINESS_RE.search(blob):
        return _ATTR_BUSINESS
    if http_ok is False or _TOOL_RE.search(blob):
        return _ATTR_TOOL
    if error_kind in {"execute_error", "exception", "batch_partial_error"}:
        return _ATTR_TOOL
    return ""


def command_side_effect(command: Optional[str], skill_name: Optional[str] = None) -> str:
    effect = resolve_action_side_effect(skill_name, command)
    if effect:
        return effect
    from orchestrator_integration.builtin_tools.skill_tools import (
        is_mutating_skill_command,
    )

    return "write" if is_mutating_skill_command(command) else "read"


def plan_batch_execution(
    commands: Sequence[dict],
    *,
    want_parallel: bool,
    channel: str = CHANNEL_LIGHT,
) -> Tuple[Optional[str], BatchPlan]:
    """串行批次保留输入顺序（FR02.1）；仅无副作用约束并行，不做全局 read/write 重排。"""
    cmds = [c for c in commands if isinstance(c, dict)]
    if not cmds:
        return "命令列表为空", BatchPlan([], False, ())

    effects = [
        command_side_effect(c.get("command"), c.get("skill_name")) for c in cmds
    ]
    # execute（跑用例/API）有副作用，不可与写操作并行（FR06.1）
    has_write = any(e in ("write", "irreversible", "execute") for e in effects)
    has_irreversible = any(e == "irreversible" for e in effects)
    notes: List[str] = []

    # FR02.1：严格保留输入顺序
    ordered = list(cmds)

    # 不可逆不在末尾且其后仍有其它动作 → 拒绝计划（不做重排）
    if has_irreversible:
        last_irr = max(i for i, e in enumerate(effects) if e == "irreversible")
        if last_irr < len(ordered) - 1:
            return (
                "约束冲突：irreversible 动作必须位于批次末尾；"
                "当前输入顺序不满足，已拒绝该计划（不自动重排）。",
                BatchPlan(ordered, False, ("irreversible_not_last",)),
            )

    if channel == CHANNEL_LIGHT and has_irreversible:
        return (
            "轻量通道禁止 irreversible 动作；请走重量通道/草稿确认。",
            BatchPlan(ordered, False, tuple(notes)),
        )

    parallel = bool(want_parallel) and len(ordered) > 1 and not has_write
    if want_parallel and has_write:
        notes.append("含写入/不可逆，已强制串行（保留输入顺序）")
        parallel = False
    if channel == CHANNEL_HEAVY and parallel:
        notes.append("重量通道默认串行")
        parallel = False
    if not notes:
        notes.append("已按输入顺序执行（未做 read/write 全局重排）")

    return None, BatchPlan(ordered, parallel, tuple(notes))


def light_channel_retry_allowed(attempt: int, channel: str) -> bool:
    """轻量通道：失败最多再试 1 次。"""
    if channel != CHANNEL_LIGHT:
        return False
    return attempt < 1


def resolve_dependent_skips(
    steps: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """依赖失败时跳过后续步骤（FR02.2 / FR02.4）。

    每个 step 形如:
      {step_id, depends_on: [...], on_failure: stop_dependents|continue, status}
    返回带 status=skipped 标注的副本。
    """
    by_id = {str(s.get("step_id") or ""): dict(s) for s in steps if s}
    failed: set = set()
    for sid, step in by_id.items():
        st = str(step.get("status") or "")
        if st in {"failed", "cancelled", "blocked"}:
            failed.add(sid)

    out: List[Dict[str, Any]] = []
    for raw in steps:
        step = dict(raw)
        sid = str(step.get("step_id") or "")
        deps = [str(d) for d in (step.get("depends_on") or []) if d]
        on_fail = str(step.get("on_failure") or "stop_dependents")
        cur = str(step.get("status") or "pending")
        if cur in {"succeeded", "failed", "cancelled", "blocked", "skipped"}:
            out.append(step)
            continue
        blocked_by = [d for d in deps if d in failed]
        if blocked_by and on_fail == "stop_dependents":
            step["status"] = "skipped"
            step["skip_reason"] = f"dependency_failed:{','.join(blocked_by)}"
            failed.add(sid)
        out.append(step)
    return out


def evaluate_browser_checkpoints(
    joined: str,
    *,
    auth_scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """浏览器上传/加购逐项验收（FR03.5 / FR03.6 / AC26 / FLOW06）。"""
    text = joined or ""
    typed = [
        m
        for m in _TYPED_ID_RE.finditer(text)
        if str(m.group(2) or "").strip().lower() not in _PLACEHOLDER_ID_VALUES
    ]
    negated = bool(_NEGATED_SUCCESS_RE.search(text))
    # 否定/失败语境中的成功关键词不算成功证据；unknown 标识不算成功
    has_success = bool(typed) and not negated
    if not has_success and not negated and not _BROWSER_TIMEOUT_RE.search(text):
        if re.search(
            r"已加入购物车|Add To Cart ok|preview\s*(refresh|已刷新)|预览刷新",
            text,
            re.I,
        ) and not re.search(r"\bfailed\b|失败", text, re.I):
            has_success = True
    if (_BROWSER_TIMEOUT_RE.search(text) or re.search(r"\bfailed\b|失败", text, re.I)) and not typed:
        has_success = False

    upload_failed = bool(
        re.search(
            r"upload\s*failed|上传失败|传图失败|file\s*upload\s*failed",
            text,
            re.I,
        )
    )
    checks = {
        "has_timeout_or_tool_error": bool(_BROWSER_TIMEOUT_RE.search(text)),
        "has_success_marker": has_success and not upload_failed,
        "count_only_1_1": bool(_UPLOAD_COUNT_ONLY_RE.search(text)),
        "has_preview_evidence": bool(
            re.search(
                r"预览刷新|preview\s*(refresh|更新|已刷新)|素材已关联",
                text,
                re.I,
            )
        )
        and not re.search(r"preview\s*not\s*refresh|预览.*未|未刷新", text, re.I),
        "has_cart_evidence": bool(
            re.search(r"cart_id|cart_item|/cart/|加入购物车|add\s*to\s*cart", text, re.I)
        ),
        "has_typed_identifier": bool(typed),
        "has_product_link_evidence": bool(
            re.search(r"目标商品|同一商品|素材关联|custom.*(image|photo)|sku", text, re.I)
        ),
        "upload_failed": upload_failed,
        "execution_method": "",
    }
    from orchestrator_integration.tool_result import infer_execution_method

    checks["execution_method"] = infer_execution_method(text)

    reasons: List[str] = []
    ok = True

    # R04：上传失败不得被有效 cart_id 掩盖
    if upload_failed:
        ok = False
        reasons.append("上传失败：购物车标识不能证明上传检查点已通过")
        checks["has_success_marker"] = False

    # AC06：仅超时文本不能验收通过
    if checks["has_timeout_or_tool_error"] and not checks["has_success_marker"]:
        ok = False
        reasons.append("浏览器工具超时/失败且无成功业务证据，验收不通过")

    # FLOW06：失败叙述 + unknown 标识不得过关
    if re.search(r"\bfailed\b|失败", text, re.I) and not checks["has_typed_identifier"]:
        ok = False
        if "业务失败证据存在" not in "".join(reasons):
            reasons.append("业务失败证据存在且无有效类型化标识，验收不通过")

    # AC24/AC25：仅 1/1 计数不够；filechooser 失败不算原生选择器成功
    if checks["execution_method"] == "filechooser_failed":
        ok = False
        reasons.append("filechooser 失败：未证明原生文件选择器上传成功")
    if checks["count_only_1_1"] and not checks["has_preview_evidence"]:
        ok = False
        reasons.append("仅计数 1/1 不能证明预览已刷新或素材正确")

    # AC26：有购物车动作时要求类型化标识；缺标识则未完全完成（AC29）
    if checks["has_cart_evidence"] and not checks["has_typed_identifier"]:
        ok = False
        reasons.append("加购有动作证据但未捕获类型化标识（cart_id/cart_item_id 等）")

    # D03/RC18：占位编号与静态 Preview 文案不得当作完成证据
    if re.search(
        r"cart_id\s*[:=]\s*(observed_cart_action|placeholder|static_preview)\b",
        text,
        re.I,
    ):
        ok = False
        reasons.append("cart_id 为占位观察值，不能作为真实购物车标识")
        checks["has_typed_identifier"] = False
    if _STATIC_PREVIEW_ONLY_RE.search(text) and not checks["has_typed_identifier"]:
        ok = False
        reasons.append("静态 Preview 文案不能证明预览/加购已完成")

    # R05：原生要求必须有允许方式的明确证据，未知方式保持未完成
    scope = auth_scope or {}
    req = str(scope.get("upload_method_requirement") or "")
    if req == "native_or_filechooser":
        method = checks["execution_method"]
        if method == "set_input":
            ok = False
            reasons.append("要求原生文件选择器/filechooser，setInputFiles/set_input 不满足")
        elif method not in {"native_dialog", "filechooser"}:
            ok = False
            reasons.append(
                "要求原生文件选择器/filechooser，但缺少允许上传方式的明确证据"
            )

    return {"ok": ok, "reasons": reasons, "checks": checks}


def evaluate_delivery_evidence(
    *,
    tool_results: Sequence[str],
    auth_scope: Optional[Dict[str, Any]] = None,
    target_url: str = "",
    stage_plan: Optional[Sequence[Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    evidence_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """交付门禁（FR09.5/9.6 / FR01.5 / AC27–AC30 / RC21–RC22）。"""
    joined = "\n".join(str(x) for x in tool_results if x)
    ok = True
    reasons: List[str] = []
    blockers: List[Dict[str, Any]] = []
    scope = auth_scope or {}
    facts = facts if isinstance(facts, dict) else {}
    confirmed_inputs: List[str] = []
    success_products: List[Dict[str, Any]] = []

    # AC28 / RC17：知识库/文档示例编号不得当作本次结果
    if re.search(
        r"(示例|样例|example|文档).{0,16}(order_sn|订单号|cart_id|cart_item_id)|"
        r"不得当作本次",
        joined,
        re.I,
    ):
        if re.search(
            r"(交付|结果|完成|成功).{0,24}"
            r"(order_sn|cart_id|cart_item_id|订单号)\s*[:=]",
            joined,
            re.I,
        ) or re.search(
            r"(order_sn|cart_id|cart_item_id)\s*[:=]\s*"
            r"(示例|样例|sample|example|demo)[\w-]*",
            joined,
            re.I,
        ):
            ok = False
            reasons.append("文档/知识库示例编号不得作为本次执行结果")
            blockers.append({"blocker_code": "kb_sample_typed_id", "step": "deliver"})

    # RC17：缺失素材却宣称上传成功
    if re.search(r"(素材|文件|图片).{0,12}(缺失|不存在|找不到|不可读)", joined) and re.search(
        r"(上传成功|已上传|upload\s*succeeded)", joined, re.I
    ):
        ok = False
        reasons.append("素材缺失时不得宣称上传成功")
        blockers.append({"blocker_code": "missing_asset_claimed_upload", "step": "upload"})

    # RC17：旧 cookie / 未探测会话不得当成本轮可用事实
    if re.search(
        r"(旧|历史|上一轮).{0,8}(cookie|会话|session)", joined, re.I
    ) and re.search(
        r"(仍在|仍有效|可复用|直接继续|已登录)", joined
    ):
        if "本轮探测" not in joined and "observed_at" not in joined:
            ok = False
            reasons.append("旧 cookie/会话不得未探测即当成本轮可用事实")
            blockers.append({"blocker_code": "stale_cookie_as_fact", "step": "probe"})

    # RC17：类目经验不得当作目标 SKU 能力/完成证据
    if re.search(
        r"(同类|这类商品|同类目|类目经验|手提袋这类).{0,24}"
        r"(已启用|支持|可用|因此).{0,24}"
        r"(上传|加购|完成|成功)",
        joined,
    ):
        if "本商品" not in joined and "实际配置" not in joined and "待验证" not in joined:
            ok = False
            reasons.append("类目经验不得当作目标 SKU 能力或完成证据")
            blockers.append(
                {"blocker_code": "category_experience_as_capability", "step": "deliver"}
            )

    # RC17：别的 SKU 预览不得替代当前目标完成
    if re.search(r"(别的|其他|另一).{0,6}(SKU|商品|sku).{0,12}(预览|preview)", joined, re.I):
        if re.search(r"(已完成|加购成功|上传成功|任务完成)", joined):
            ok = False
            reasons.append("其他 SKU 预览不得作为当前目标完成证据")
            blockers.append({"blocker_code": "other_sku_preview", "step": "verify"})

    # AC27：仅凭 143/dnd 域名差不断言不支持
    if re.search(r"(仅有|只有).{0,8}(143|dnd).{0,20}(不支持|不同于)", joined, re.I):
        if "核对" not in joined and "实际配置" not in joined and "待验证" not in joined:
            ok = False
            reasons.append("环境支持范围须核对目标商品实际配置，不能仅凭域名命名断言")
            blockers.append({"blocker_code": "env_inference_unverified", "step": "kb"})

    # AC30 / R30：其他商品观察或证据 URL 与目标不一致均不得过关
    if re.search(r"(另一|其他).{0,6}商品|所见所得", joined) and target_url:
        if target_url.rstrip("/") not in joined.replace(target_url, "", 1):
            if "同一商品" not in joined and "等价样本" not in joined:
                ok = False
                reasons.append("其他商品观察不能替代当前目标商品验证，禁止静默换商品")
                blockers.append({"blocker_code": "wrong_product_evidence", "step": "verify"})

    if target_url:
        from orchestrator_integration.tool_result import extract_source_url

        evidence_url = extract_source_url(joined)
        target_norm = target_url.rstrip("/")
        if evidence_url:
            evid_norm = evidence_url.rstrip("/")
            if target_norm not in evid_norm and evid_norm not in target_norm:
                # 证据指向其它商品/页面
                if target_norm not in joined:
                    ok = False
                    reasons.append("购物车/预览证据 URL 与目标商品不一致")
                    blockers.append(
                        {"blocker_code": "evidence_url_mismatch", "step": "verify"}
                    )
        elif re.search(r"cart_id|preview|加购|上传", joined, re.I):
            # 有交付叙述但无关联目标 URL/SKU
            product_markers = re.findall(
                r"(?:product[-_/]|sku[=:]\s*|goods[-_/])([A-Za-z0-9_-]+)",
                joined,
                re.I,
            )
            target_markers = re.findall(
                r"(?:product[-_/]|sku[=:]\s*|goods[-_/])([A-Za-z0-9_-]+)",
                target_url,
                re.I,
            )
            if product_markers and target_markers:
                if not set(m.lower() for m in product_markers) & set(
                    m.lower() for m in target_markers
                ):
                    ok = False
                    reasons.append("交付证据商品标识与目标商品不一致")
                    blockers.append(
                        {"blocker_code": "evidence_product_mismatch", "step": "verify"}
                    )

    # 授权边界：只授权加购时若出现提交订单证据 → 越权
    allow = set(scope.get("allow_actions") or [])
    deny = set(scope.get("deny_actions") or [])
    if ("submit_order" in deny or "pay" in deny) and re.search(
        r"submit[_ ]?order|提交订单|下单成功", joined, re.I
    ):
        if "add_to_cart" in allow and "submit_order" not in allow:
            ok = False
            reasons.append("仅授权加购时不得提交订单（即使用户禁止支付）")
            blockers.append({"blocker_code": "order_beyond_auth", "step": "checkout"})

    # 证据硬门禁：只拦甩锅恢复环境 / 未探测宣称可用 / 占位字段
    # 措辞类（进度标题、能力推断、替换入口）记 soft，不否决交付
    try:
        from orchestrator_integration.evidence_integrity import (
            count_browser_actions,
            evaluate_evidence_integrity_text,
        )

        integrity = evaluate_evidence_integrity_text(
            joined,
            probe_ok_to_reuse=None,
            browser_actions_this_turn=count_browser_actions(tool_results),
        )
        if not integrity.get("hard_ok", integrity.get("ok")):
            ok = False
            for r in integrity.get("hard_reasons") or integrity.get("reasons") or []:
                if r not in reasons:
                    reasons.append(r)
            blockers.append(
                {
                    "blocker_code": "evidence_integrity",
                    "step": "deliver",
                    "http_freshness": integrity.get("http_freshness"),
                }
            )
    except Exception:
        pass

    # 未捕获却承诺全量购物车字段 → 过满交付
    if re.search(
        r"(返回|交付|给出).{0,24}(所有|全部).{0,16}(cart_id|数量|金额|购物车)",
        joined,
        re.I,
    ) and not re.search(r"cart_id\s*[:=]\s*[A-Za-z0-9_-]{4,}", joined, re.I):
        ok = False
        reasons.append("尚未实际捕获时不得承诺交付全部购物车标识/数量/金额")
        blockers.append({"blocker_code": "delivery_overpromise", "step": "deliver"})

    # RC22：无本轮浏览器动作却宣称加购/上传完成 → hard fail
    try:
        from orchestrator_integration.evidence_integrity import count_browser_actions

        actions = count_browser_actions(tool_results)
    except Exception:
        actions = 0
    claims_exec = bool(
        re.search(
            r"(已完成|已加购|加购成功|上传成功|已上传|任务完成|执行完成)",
            joined,
        )
    )
    if actions == 0 and claims_exec and re.search(
        r"(加购|上传|购物车|cart|upload)", joined, re.I
    ):
        ok = False
        reasons.append("本轮无业务动作，不得将已整理输入标为已完成执行")
        blockers.append({"blocker_code": "input_only_claimed_complete", "step": "deliver"})
        for m in re.findall(
            r"(目标\s*URL|商品链接|素材路径|账号|环境)\s*[:=：]\s*\S+", joined
        ):
            confirmed_inputs.append(m)

    # RC22：证据 epoch 不匹配的旧 cart_id
    epoch_now = (
        int(evidence_epoch)
        if evidence_epoch is not None
        else int(facts.get("evidence_epoch") or 0)
    )
    stale_ids = facts.get("stale_typed_ids") or []
    if isinstance(stale_ids, list) and stale_ids:
        for sid in stale_ids:
            if sid and str(sid) in joined:
                ok = False
                reasons.append(f"旧证据标识 {sid} 已随纠正失效（epoch={epoch_now}）")
                blockers.append(
                    {
                        "blocker_code": "stale_typed_id",
                        "step": "deliver",
                        "id": str(sid),
                        "evidence_epoch": epoch_now,
                    }
                )

    # 本轮真实捕获的 typed id → success_products / confirmed
    for m in _TYPED_ID_RE.finditer(joined):
        key, val = m.group(1).lower(), m.group(2)
        if val.lower() in _PLACEHOLDER_ID_VALUES:
            continue
        success_products.append(
            {
                "type": key,
                "id": val,
                "evidence_epoch": epoch_now,
            }
        )
        confirmed_inputs.append(f"{key}={val}")

    # RC21：剩余检查点
    remaining_checkpoints: List[str] = []
    completed_ids = set(facts.get("completed_stage_ids") or [])
    for raw in stage_plan or []:
        if isinstance(raw, dict):
            sid = str(raw.get("stage_id") or "")
            if sid and sid not in completed_ids and sid != "summarize":
                remaining_checkpoints.append(sid)
        elif isinstance(raw, str) and raw not in completed_ids:
            remaining_checkpoints.append(raw)

    if not confirmed_inputs and target_url:
        confirmed_inputs.append(f"target_url={target_url}")

    return {
        "ok": ok,
        "reasons": reasons,
        "blockers": blockers,
        "partial_ok": bool(joined.strip()) and not ok,
        "success_products": success_products,
        "confirmed_inputs": confirmed_inputs,
        "remaining_checkpoints": remaining_checkpoints,
        "evidence_epoch": epoch_now,
    }


def evaluate_post_condition(
    *,
    post_condition: str,
    tool_results: Sequence[str],
    had_unconfirmed_write: bool = False,
    playbook_id: str = "",
    auth_scope: Optional[Dict[str, Any]] = None,
    target_url: str = "",
    stage_plan: Optional[Sequence[Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    evidence_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """确定性后置断言（不靠模型自评）。"""
    joined = "\n".join(str(x) for x in tool_results if x)
    ok = True
    reasons: List[str] = []
    extra: Dict[str, Any] = {}
    blockers: List[Dict[str, Any]] = []

    if had_unconfirmed_write:
        ok = False
        reasons.append("存在未确认写入，退出断言失败")

    if playbook_id == "url_read":
        if not joined.strip():
            ok = False
            reasons.append("url-reader 无输出")
        elif "needs_confirmation" in joined and "draft" in joined:
            pass
    elif playbook_id == "testcase_execute":
        if "不在本轮分区" in joined or "越权" in joined:
            ok = False
            reasons.append("执行区门禁拒绝")
        if "needs_confirmation" in joined and "create_ui" in joined.lower():
            pass
        # 超时且无执行记录不得通过
        if re.search(r"timeout|超时", joined, re.I) and not re.search(
            r"execution_id|终态|get_execution_records", joined, re.I
        ):
            ok = False
            reasons.append("用例执行超时且无执行记录，验收不通过")
        if _BUSINESS_RE.search(joined) and not re.search(
            r'"status"\s*:\s*"succeeded"|终态.*passed', joined, re.I
        ):
            ok = False
            reasons.append("业务拒绝不得记为成功")
    elif playbook_id == "platform_write":
        # 字段名 error_code 不是失败；仅真实失败态才拒绝
        scrubbed = re.sub(
            r"error[_-]?code\s*[\"']?\s*[:=]\s*[\"']?[^,\"'\s}\]]*",
            "",
            joined,
            flags=re.I,
        )
        scrubbed = re.sub(r'"error_code"\s*:\s*[^,}\]]+', "", scrubbed, flags=re.I)
        real_fail = bool(
            re.search(
                r'"status"\s*:\s*"(failed|error)"|"ok"\s*:\s*false|"success"\s*:\s*false',
                joined,
                re.I,
            )
        ) or (
            "needs_confirmation" not in joined
            and bool(re.search(r"(?<![_\w])error(?![_\w])", scrubbed, re.I))
        )
        if real_fail and "needs_confirmation" not in joined:
            ok = False
            reasons.append("写入路径异常且无确认草稿")
    elif playbook_id == "browser_act":
        browser = evaluate_browser_checkpoints(joined, auth_scope=auth_scope)
        extra["browser_checks"] = browser.get("checks")
        if not browser.get("ok", True):
            ok = False
            reasons.extend(browser.get("reasons") or [])
        elif not joined.strip():
            ok = False
            reasons.append("浏览器任务无工具结果可用于断言")

    delivery = evaluate_delivery_evidence(
        tool_results=tool_results,
        auth_scope=auth_scope,
        target_url=target_url,
        stage_plan=stage_plan,
        facts=facts,
        evidence_epoch=evidence_epoch,
    )
    if not delivery.get("ok", True):
        ok = False
        reasons.extend(delivery.get("reasons") or [])
        blockers.extend(delivery.get("blockers") or [])
    extra["delivery"] = delivery
    # RC21：透出成功产物 / 已确认输入 / 剩余检查点
    for k in (
        "success_products",
        "confirmed_inputs",
        "remaining_checkpoints",
        "evidence_epoch",
    ):
        if k in delivery:
            extra[k] = delivery[k]

    if post_condition and not reasons and not joined.strip():
        ok = False
        reasons.append("无工具结果可用于断言")

    # 浏览器：结构化 failed/unknown 且无成功证据 → 不得完成（AC06）
    if playbook_id == "browser_act" and (
        '"status": "failed"' in joined or '"status": "unknown"' in joined
    ) and not _TYPED_ID_RE.search(joined):
        ok = False
        if "工具结果含 failed/unknown 且无成功证据" not in reasons:
            reasons.append("工具结果含 failed/unknown 且无成功证据")

    # 业务拒绝一律不得标成功（任意剧本）
    if _BUSINESS_RE.search(joined) and not re.search(
        r'"status"\s*:\s*"succeeded"|业务成功|验收通过', joined, re.I
    ):
        ok = False
        if "业务拒绝不得记为成功" not in reasons:
            reasons.append("业务拒绝不得记为成功")

    return {
        "ok": ok,
        "post_condition": post_condition,
        "reasons": reasons,
        "blockers": blockers,
        "playbook_id": playbook_id,
        **extra,
    }
