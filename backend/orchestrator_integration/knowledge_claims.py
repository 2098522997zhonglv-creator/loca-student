"""知识结论适用性（FR09.5 / FR09.6）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_ENV_NAME_RE = re.compile(r"\b(143|dnd)\b", re.I)
_ORDER_SN_SAMPLE_RE = re.compile(r"\b\d{15,20}\b")


def classify_kb_claim(
    *,
    text: str,
    source_doc: str = "",
    source_version: str = "",
    target_env: str = "",
) -> Dict[str, Any]:
    """区分文档陈述 / 推断 / 当前观察。"""
    blob = text or ""
    claim_type = "document"
    warnings: List[str] = []

    if any(k in blob for k in ("可能", "推断", "推测", "因此", "所以应该")):
        claim_type = "inference"
    if any(k in blob for k in ("本次观察", "当前页面", "我扫到", "实测")):
        claim_type = "observation"

    # 仅凭 143/dnd 域名差推断不支持 → 标记待验证
    if _ENV_NAME_RE.search(blob) and any(
        k in blob for k in ("仅", "只有", "不支持", "不同于", "分支")
    ):
        warnings.append(
            "环境支持范围需核对目标商品实际配置；域名命名差异不能直接断言不支持"
        )
        if claim_type == "document":
            claim_type = "inference"

    sample_sns = _ORDER_SN_SAMPLE_RE.findall(blob)
    return {
        "claim_type": claim_type,
        "source_doc": source_doc or "",
        "source_version": source_version or "",
        "target_env": target_env or "",
        "warnings": warnings,
        "sample_order_sns": sample_sns[:3],
        "note": "知识库订单号示例不得作为本次执行结果",
    }


def assert_target_product_consistency(
    *,
    target_url: str,
    evidence_url: str = "",
    evidence_sku: str = "",
    other_product_note: str = "",
) -> Dict[str, Any]:
    """目标商品与证据必须关联指定 URL/SKU（FR09.6）。"""
    ok = True
    reasons: List[str] = []
    if target_url and evidence_url and target_url.rstrip("/") != evidence_url.rstrip("/"):
        ok = False
        reasons.append("证据 URL 与目标商品不一致，不得替代验证")
    if other_product_note and not evidence_url:
        ok = False
        reasons.append("其他商品观察不能替代当前目标商品验证")
    return {
        "ok": ok,
        "reasons": reasons,
        "target_url": target_url or "",
        "evidence_url": evidence_url or "",
        "evidence_sku": evidence_sku or "",
    }


def format_claim_for_prompt(claim: Dict[str, Any]) -> str:
    return (
        f"[知识适用性] claim_type={claim.get('claim_type')} "
        f"source={claim.get('source_doc') or '(未知)'} "
        f"warnings={';'.join(claim.get('warnings') or []) or '无'}"
    )


def annotate_prefetch_chunk(
    *,
    content: str,
    source: str = "",
    score: float = 0.0,
    index: int = 1,
) -> str:
    """给预检索片段附加适用性标签，避免把示例订单号当成本次结果。"""
    claim = classify_kb_claim(text=content or "", source_doc=source or "")
    header = (
        f"[预检索{index}] (相似度 {score * 100:.1f}%, 来源: {source or '未知来源'}, "
        f"claim_type={claim['claim_type']})"
    )
    notes = []
    if claim.get("warnings"):
        notes.append("⚠ " + "；".join(claim["warnings"]))
    if claim.get("sample_order_sns"):
        notes.append(
            "⚠ 片段含示例订单号 "
            + ",".join(claim["sample_order_sns"])
            + " —— 不得当作本次执行结果"
        )
    if claim["claim_type"] == "inference":
        notes.append("⚠ 此为推断/适用性待核验，交付时须标明非文档原文断言")
    body = content or ""
    if notes:
        return header + "\n" + "\n".join(notes) + "\n" + body
    return header + "\n" + body


def kb_applicability_footer() -> str:
    return (
        "\n\n## 知识适用性约束\n"
        "- 文档陈述 ≠ 当前环境事实；含「仅/只有/不支持」的环境断言须核对目标商品。\n"
        "- 知识库订单号/截图示例不得当作本次执行产物。\n"
        "- 其他商品观察不能替代目标 URL/SKU 验证。\n"
    )
