"""第 4 阶段：结果证据与交付。

结论由执行结果支撑，而非模型措辞。
- 验收条件绑定证据（工具返回 / 页面状态 / DB / 文件）
- 区分已执行 / 已观察 / 已验证
- 交付列出目标、步骤、证据、失败与未完成项
- 证据缺失或矛盾时禁止 completed
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# 证据声明层级（由弱到强）
CLAIM_EXECUTED = "executed"  # 工具已调用并返回
CLAIM_OBSERVED = "observed"  # 观察到外部状态（页面/DB/文件）
CLAIM_VERIFIED = "verified"  # 对照期望完成校验

CLAIM_RANK = {
    CLAIM_EXECUTED: 1,
    CLAIM_OBSERVED: 2,
    CLAIM_VERIFIED: 3,
    # 兼容旧值
    "observation": 2,
    "document": 2,
    "inference": 1,
}

# Align with expected_business_steps_from_plan: conversation-only ``single``
# (and empty ids) are not business checkpoints and must not invent
# ``single:check:0`` missing_evidence while progress shows ``0/—``.
_NON_BUSINESS_STAGE_IDS = frozenset({"single", ""})


def _is_business_stage(stage_id: str) -> bool:
    return str(stage_id or "").strip() not in _NON_BUSINESS_STAGE_IDS


def classify_claim_level(
    *,
    tool_name: str = "",
    step_status: str = "",
    structured: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> str:
    """根据步骤结果判定证据层级。"""
    structured = structured or {}
    evidence = evidence or {}
    name = (tool_name or "").strip().lower()
    status = (step_status or structured.get("status") or "").strip().lower()

    # 显式声明优先
    explicit = (
        structured.get("claim_level")
        or structured.get("claim_type")
        or evidence.get("claim_level")
        or evidence.get("claim_type")
        or ""
    )
    if explicit in CLAIM_RANK and explicit in {
        CLAIM_EXECUTED,
        CLAIM_OBSERVED,
        CLAIM_VERIFIED,
    }:
        return str(explicit)

    if name in {"assert_task_postcondition", "assert_and_deliver"} or (
        "assert" in name and "postcondition" in name
    ):
        if status in {"succeeded", "ok", "passed"}:
            return CLAIM_VERIFIED
        return CLAIM_EXECUTED

    # 副作用前后态变化 → 已观察
    before = evidence.get("side_effect_before")
    after = evidence.get("side_effect_after")
    if before is None:
        before = (structured.get("evidence") or {}).get("side_effect_before")
    if after is None:
        after = (structured.get("evidence") or {}).get("side_effect_after")
    if before is not None and after is not None and before != after:
        return CLAIM_OBSERVED

    # 页面/资源观测字段
    data = structured.get("data") if isinstance(structured.get("data"), dict) else {}
    if any(
        data.get(k) or structured.get(k) or evidence.get(k)
        for k in (
            "page_state",
            "page_url",
            "dom_snapshot",
            "db_row",
            "file_path",
            "resource_id",
            "source_url",
        )
    ):
        if status in {"succeeded", "ok", "passed", "skipped"}:
            return CLAIM_OBSERVED

    if status in {"succeeded", "ok", "passed", "skipped"}:
        return CLAIM_EXECUTED
    if status in {"failed", "cancelled", "unknown", "blocked"}:
        return CLAIM_EXECUTED
    return CLAIM_EXECUTED


def _criteria_from_stage_plan(stage_plan: Sequence[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in stage_plan or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("stage_id") or s.get("node_id") or "")
        if not _is_business_stage(sid):
            continue
        checks = list(s.get("completion_checks") or [])
        if s.get("acceptance") and not checks:
            checks = [s.get("acceptance")]
        # 兼容 tool_auth manifest 风格
        if not checks and s.get("acceptance_evidence"):
            checks = list(s.get("acceptance_evidence") or [])
        # summarize without acceptance is not a business checkpoint
        if sid == "summarize" and not checks:
            continue
        for i, c in enumerate(checks):
            if isinstance(c, dict):
                cid = str(c.get("criterion_id") or c.get("id") or f"{sid}:{i}")
                desc = str(c.get("description") or c.get("text") or c)
                required = str(c.get("required_claim") or CLAIM_OBSERVED)
                kind = str(c.get("evidence_kind") or c.get("kind") or "tool_return")
            else:
                cid = f"{sid}:{i}"
                desc = str(c)
                required = CLAIM_OBSERVED
                kind = "tool_return"
            out.append(
                {
                    "criterion_id": cid,
                    "stage_id": sid,
                    "description": desc,
                    "required_claim": required
                    if required in CLAIM_RANK
                    else CLAIM_OBSERVED,
                    "evidence_kind": kind,  # tool_return|page_state|db_record|file
                }
            )
    return out


def _criteria_from_task_plan(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = facts.get("task_plan") if isinstance(facts.get("task_plan"), dict) else {}
    nodes = list(plan.get("nodes") or [])
    out: List[Dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("node_id") or "")
        if not _is_business_stage(nid):
            continue
        for i, c in enumerate(node.get("completion_checks") or []):
            if isinstance(c, dict):
                out.append(
                    {
                        "criterion_id": str(
                            c.get("criterion_id") or f"{nid}:check:{i}"
                        ),
                        "stage_id": nid,
                        "description": str(
                            c.get("description") or c.get("text") or c
                        ),
                        "required_claim": str(
                            c.get("required_claim") or CLAIM_OBSERVED
                        ),
                        "evidence_kind": str(
                            c.get("evidence_kind") or "tool_return"
                        ),
                    }
                )
            else:
                out.append(
                    {
                        "criterion_id": f"{nid}:check:{i}",
                        "stage_id": nid,
                        "description": str(c),
                        "required_claim": CLAIM_OBSERVED,
                        "evidence_kind": "tool_return",
                    }
                )
    return out


def list_acceptance_criteria(task) -> List[Dict[str, Any]]:
    """收集任务验收条件（阶段计划 + 编排计划）。"""
    facts = dict(getattr(task, "facts", None) or {})
    criteria = _criteria_from_task_plan(facts)
    if not criteria:
        criteria = _criteria_from_stage_plan(
            list(getattr(task, "stage_plan", None) or [])
        )
    # 去重
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for c in criteria:
        cid = c["criterion_id"]
        if cid in seen:
            continue
        seen.add(cid)
        uniq.append(c)
    return uniq


def _step_rows(task) -> List[Dict[str, Any]]:
    from orchestrator_integration.models import AgentStep

    rows = list(
        AgentStep.objects.filter(task=task)
        .order_by("step_number")
        .values(
            "step_number",
            "step_id",
            "stage_id",
            "status",
            "tool_name",
            "tool_output_summary",
            "operation_id",
            "structured_result",
            "evidence",
        )
    )
    for r in rows:
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        st = r.get("structured_result") if isinstance(r.get("structured_result"), dict) else {}
        level = classify_claim_level(
            tool_name=r.get("tool_name") or "",
            step_status=r.get("status") or "",
            structured=st,
            evidence=ev,
        )
        r["claim_level"] = level
        r["output_ref"] = ev.get("output_ref") or st.get("output_ref") or ""
        r["evidence_id"] = ev.get("evidence_id") or st.get("evidence_id")
        r["side_effect_before"] = ev.get("side_effect_before")
        r["side_effect_after"] = ev.get("side_effect_after")
    return rows


def bind_criteria_to_evidence(
    task,
    *,
    criteria: Optional[Sequence[Dict[str, Any]]] = None,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """将验收条件绑定到步骤证据。"""
    criteria = list(criteria if criteria is not None else list_acceptance_criteria(task))
    steps = list(steps if steps is not None else _step_rows(task))
    bindings: List[Dict[str, Any]] = []

    for c in criteria:
        sid = c.get("stage_id") or ""
        stage_steps = [
            s for s in steps if (not sid) or str(s.get("stage_id") or "") == sid
        ]
        # 优先同阶段；否则全局
        candidates = stage_steps or list(steps)
        best = None
        for s in reversed(candidates):  # 最近步骤优先
            st = str(s.get("status") or "")
            if st in {"failed", "cancelled", "unknown", "blocked"}:
                # 失败步骤也可作为「已执行」证据，但不能满足 verified
                if best is None:
                    best = s
                continue
            if st in {"succeeded", "skipped", "ok", "passed"}:
                best = s
                break
        claim = (best or {}).get("claim_level") or ""
        required = c.get("required_claim") or CLAIM_OBSERVED
        rank_ok = CLAIM_RANK.get(claim, 0) >= CLAIM_RANK.get(required, 2)
        has_ref = bool((best or {}).get("output_ref") or (best or {}).get("evidence_id"))
        bindings.append(
            {
                **c,
                "bound": best is not None and has_ref,
                "claim_level": claim or None,
                "claim_ok": bool(rank_ok and has_ref and best),
                "step_id": (best or {}).get("step_id"),
                "step_status": (best or {}).get("status"),
                "tool_name": (best or {}).get("tool_name"),
                "output_ref": (best or {}).get("output_ref") or "",
                "evidence_id": (best or {}).get("evidence_id"),
                "summary": ((best or {}).get("tool_output_summary") or "")[:500],
            }
        )
    return bindings


def detect_evidence_contradictions(
    steps: Sequence[Dict[str, Any]],
    bindings: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """检测证据矛盾：工具成功但状态未变；期望≠观察；声明 verified 但无观察。"""
    issues: List[Dict[str, Any]] = []
    for s in steps:
        status = str(s.get("status") or "")
        before = s.get("side_effect_before")
        after = s.get("side_effect_after")
        # 验证方法1：工具返回成功，但页面/业务状态未改变
        st = s.get("structured_result") if isinstance(s.get("structured_result"), dict) else {}
        if (
            status in {"succeeded", "ok", "passed"}
            and before is not None
            and after is not None
            and before == after
            and not st.get("no_side_effect")
            and not st.get("read_only")
        ):
            issues.append(
                {
                    "code": "success_but_state_unchanged",
                    "step_id": s.get("step_id"),
                    "tool_name": s.get("tool_name"),
                    "message": (
                        "工具返回成功，但观测到的业务/页面状态未改变"
                        f"（before={before} after={after}）。"
                    ),
                    "before": before,
                    "after": after,
                }
            )

        # expected vs observed on structured evidence
        st = s.get("structured_result") if isinstance(s.get("structured_result"), dict) else {}
        expected = st.get("expected")
        observed = st.get("observed")
        if expected is not None and observed is not None and expected != observed:
            issues.append(
                {
                    "code": "expected_observed_mismatch",
                    "step_id": s.get("step_id"),
                    "tool_name": s.get("tool_name"),
                    "message": f"期望与观察不一致：expected={expected} observed={observed}",
                    "expected": expected,
                    "observed": observed,
                }
            )

        if (s.get("claim_level") == CLAIM_VERIFIED) and not (
            s.get("output_ref") or s.get("evidence_id")
        ):
            issues.append(
                {
                    "code": "verified_without_evidence",
                    "step_id": s.get("step_id"),
                    "tool_name": s.get("tool_name"),
                    "message": "声明已验证但缺少可追溯证据引用",
                }
            )

    for b in bindings or []:
        if b.get("claim_ok") and b.get("required_claim") == CLAIM_VERIFIED:
            if (b.get("claim_level") or "") != CLAIM_VERIFIED:
                issues.append(
                    {
                        "code": "claim_below_required",
                        "criterion_id": b.get("criterion_id"),
                        "message": (
                            f"条件 {b.get('criterion_id')} 需要 verified，"
                            f"实际为 {b.get('claim_level')}"
                        ),
                    }
                )
    return issues


def build_delivery_report(
    task,
    *,
    bindings: Optional[Sequence[Dict[str, Any]]] = None,
    steps: Optional[Sequence[Dict[str, Any]]] = None,
    contradictions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """交付报告：目标、步骤结果、证据、失败项、未完成项。"""
    steps = list(steps if steps is not None else _step_rows(task))
    bindings = list(
        bindings
        if bindings is not None
        else bind_criteria_to_evidence(task, steps=steps)
    )
    contradictions = list(
        contradictions
        if contradictions is not None
        else detect_evidence_contradictions(steps, bindings)
    )

    completed_items = [
        {
            "criterion_id": b["criterion_id"],
            "description": b.get("description"),
            "claim_level": b.get("claim_level"),
            "output_ref": b.get("output_ref"),
            "evidence_id": b.get("evidence_id"),
            "step_id": b.get("step_id"),
        }
        for b in bindings
        if b.get("claim_ok")
    ]
    incomplete_items = [
        {
            "criterion_id": b["criterion_id"],
            "description": b.get("description"),
            "reason": (
                "missing_evidence"
                if not b.get("bound")
                else f"claim_insufficient:{b.get('claim_level')}"
            ),
            "required_claim": b.get("required_claim"),
            "claim_level": b.get("claim_level"),
        }
        for b in bindings
        if not b.get("claim_ok")
    ]
    failures = [
        {
            "step_id": s.get("step_id"),
            "stage_id": s.get("stage_id"),
            "tool_name": s.get("tool_name"),
            "status": s.get("status"),
            "summary": (s.get("tool_output_summary") or "")[:500],
            "output_ref": s.get("output_ref") or "",
        }
        for s in steps
        if str(s.get("status") or "") in {"failed", "cancelled", "unknown", "blocked"}
    ]

    step_results = [
        {
            "step_id": s.get("step_id"),
            "stage_id": s.get("stage_id"),
            "status": s.get("status"),
            "tool_name": s.get("tool_name"),
            "claim_level": s.get("claim_level"),
            "output_ref": s.get("output_ref") or "",
            "summary": (s.get("tool_output_summary") or "")[:300],
        }
        for s in steps
    ]

    ok = (
        len(incomplete_items) == 0
        and len(contradictions) == 0
        and (len(bindings) == 0 or len(completed_items) == len(bindings))
        and not any(f.get("status") == "failed" for f in failures)
    )
    # 无准则时：至少要有一步 verified/observed 成功证据才允许完成由上游决定；
    # 此处 ok 仅表示「证据门禁未发现缺口」
    if not bindings:
        ok = len(contradictions) == 0 and not any(
            f.get("status") == "failed" for f in failures
        )

    return {
        "goal": getattr(task, "goal", "") or "",
        "task_id": getattr(task, "public_task_id", None) or str(getattr(task, "pk", "")),
        "status": getattr(task, "status", "") or "",
        "step_results": step_results,
        "evidence_bindings": bindings,
        "completed_items": completed_items,
        "incomplete_items": incomplete_items,
        "failures": failures,
        "contradictions": contradictions,
        "ok": ok,
        "summary": {
            "criteria_total": len(bindings),
            "criteria_ok": len(completed_items),
            "failures": len(failures),
            "contradictions": len(contradictions),
        },
    }


def gate_completion_on_evidence(
    task,
    acceptance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """完成门禁：证据缺失或矛盾 → 禁止 completed。

    返回：
      ok: 是否允许 completed
      acceptance: 修订后的 acceptance（失败时 ok=False）
      delivery: 交付报告
    """
    acceptance = dict(acceptance or getattr(task, "acceptance", None) or {})
    steps = _step_rows(task)
    bindings = bind_criteria_to_evidence(task, steps=steps)
    contradictions = detect_evidence_contradictions(steps, bindings)
    delivery = build_delivery_report(
        task,
        bindings=bindings,
        steps=steps,
        contradictions=contradictions,
    )

    reasons: List[str] = list(acceptance.get("reasons") or [])
    blockers: List[Dict[str, Any]] = list(acceptance.get("blockers") or [])
    allow_complete = True

    if contradictions:
        allow_complete = False
        for c in contradictions:
            msg = c.get("message") or c.get("code") or "evidence_contradiction"
            reasons.append(str(msg))
            blockers.append(
                {
                    "blocker_code": c.get("code") or "evidence_contradiction",
                    "step": "evidence_gate",
                    "detail": c,
                }
            )

    # 有明确验收条件时，必须全部 claim_ok
    if bindings and delivery.get("incomplete_items"):
        allow_complete = False
        for item in delivery["incomplete_items"]:
            reasons.append(
                f"验收条件缺少合格证据: {item.get('criterion_id')} "
                f"({item.get('reason')})"
            )
            blockers.append(
                {
                    "blocker_code": "missing_evidence",
                    "step": "evidence_gate",
                    "criterion_id": item.get("criterion_id"),
                    "recovery": "补齐工具/页面/DB/文件证据后发送「继续」；勿仅凭模型措辞完成。",
                }
            )

    # 上游已 ok=False 则保持
    if acceptance.get("ok") is False:
        allow_complete = False

    # 上游声称 ok，但门禁失败 → 翻转为 blocked
    if acceptance.get("ok") and not allow_complete:
        acceptance = dict(acceptance)
        acceptance["ok"] = False
        acceptance["reasons"] = list(dict.fromkeys(reasons))
        acceptance["blockers"] = blockers
        acceptance["recovery"] = (
            "证据缺失或互相矛盾，禁止标记完成；"
            "核对实际页面/DB/文件状态后补证或修复，再发送「继续」。"
        )
        acceptance["delivery"] = delivery
        acceptance["evidence_gate"] = {
            "ok": False,
            "contradictions": contradictions,
            "incomplete": delivery.get("incomplete_items") or [],
        }
    elif allow_complete:
        acceptance = dict(acceptance)
        acceptance.setdefault("ok", True)
        acceptance["delivery"] = delivery
        acceptance["evidence_gate"] = {"ok": True}
    else:
        acceptance = dict(acceptance)
        acceptance["ok"] = False
        acceptance["reasons"] = list(dict.fromkeys(reasons)) or [
            "证据门禁未通过"
        ]
        acceptance["blockers"] = blockers
        acceptance["delivery"] = delivery
        acceptance["evidence_gate"] = {"ok": False}

    # 持久化交付快照
    try:
        facts = dict(getattr(task, "facts", None) or {})
        facts["delivery_report"] = delivery
        task.facts = facts
        type(task).objects.filter(pk=task.pk).update(facts=facts)
    except Exception:
        pass

    return {
        "ok": bool(allow_complete and acceptance.get("ok")),
        "acceptance": acceptance,
        "delivery": delivery,
        "contradictions": contradictions,
        "bindings": bindings,
    }


def persist_step_claim_level(step, claim_level: str) -> None:
    """回写步骤 evidence.claim_level。"""
    try:
        ev = dict(getattr(step, "evidence", None) or {})
        ev["claim_level"] = claim_level
        step.evidence = ev
        type(step).objects.filter(pk=step.pk).update(evidence=ev)
    except Exception:
        pass
