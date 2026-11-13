"""任务执行配置快照（D04 / FR04/07/08 · 第5–8节）。

配置只是输入：业务预期步数、工具预算、Skill/动作授权分别存储并共同校验。
本模块生成 effective_config，写入 AgentTask.budget / auth_scope，供 Runtime 读取。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = 1

# 服务端硬上限（不可被用户/模型抬高）
SERVER_HARD_CAPS = {
    "max_tool_actions": 200,
    "max_attempts_per_step": 10,
    "max_model_calls": 80,
    "max_tokens": 500_000,
    "max_duration_seconds": 7200,
    "max_no_progress_repeats": 10,
    "max_replans": 5,
}

DEFAULT_TEMPLATE = {
    "max_tool_actions": 30,
    "max_attempts_per_step": 3,
    "max_model_calls": 15,
    "max_tokens": 50_000,
    "max_duration_seconds": 900,
    "max_no_progress_repeats": 3,
    "max_replans": 2,
}


def _as_pos_int(value: Any, default: int, *, hard_cap: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if n <= 0:
        n = default
    return min(n, hard_cap)


def expected_business_steps_from_plan(stage_plan: Sequence[Any]) -> int:
    """由启用的可验收检查点派生；不含空/summarize-only 噪声。"""
    n = 0
    for raw in stage_plan or []:
        if isinstance(raw, dict):
            sid = str(raw.get("stage_id") or "")
            acceptance = str(raw.get("acceptance") or "").strip()
        else:
            sid = str(getattr(raw, "stage_id", "") or "")
            acceptance = str(getattr(raw, "acceptance", "") or "").strip()
        if not sid or sid in {"single", "summarize"}:
            # summarize 也可计为检查点，但无 acceptance 时不计入
            if sid == "summarize" and acceptance:
                n += 1
            continue
        n += 1
    return max(n, 1) if stage_plan else 0


def build_effective_config(
    *,
    stage_plan: Optional[Sequence[Any]] = None,
    auth_scope: Optional[Dict[str, Any]] = None,
    commission: Any = None,
    overrides: Optional[Dict[str, Any]] = None,
    policy_caps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成不可抬高服务端上限的有效配置快照。"""
    caps = dict(SERVER_HARD_CAPS)
    if policy_caps:
        for k, v in policy_caps.items():
            if k in caps:
                try:
                    caps[k] = min(int(caps[k]), int(v))
                except (TypeError, ValueError):
                    pass

    ov = dict(overrides or {})
    tmpl = dict(DEFAULT_TEMPLATE)

    if commission is not None:
        mt = int(getattr(commission, "max_tool_steps", 0) or 0)
        if mt:
            tmpl["max_tool_actions"] = mt
        tok = int(getattr(commission, "max_tokens", 0) or 0)
        if tok:
            tmpl["max_tokens"] = tok
        dur = int(getattr(commission, "max_duration_ms", 0) or 0)
        if dur:
            tmpl["max_duration_seconds"] = max(1, dur // 1000)

    for key in DEFAULT_TEMPLATE:
        if key in ov and ov[key] is not None:
            tmpl[key] = ov[key]

    effective = {
        "schema_version": SCHEMA_VERSION,
        "config_version": 1,
        "expected_business_steps": expected_business_steps_from_plan(stage_plan or []),
        "completed_business_steps": 0,
        "max_tool_actions": _as_pos_int(
            tmpl["max_tool_actions"], 30, hard_cap=caps["max_tool_actions"]
        ),
        "max_attempts_per_step": _as_pos_int(
            tmpl["max_attempts_per_step"], 3, hard_cap=caps["max_attempts_per_step"]
        ),
        "max_model_calls": _as_pos_int(
            tmpl["max_model_calls"], 15, hard_cap=caps["max_model_calls"]
        ),
        "max_tokens": _as_pos_int(
            tmpl["max_tokens"], 50_000, hard_cap=caps["max_tokens"]
        ),
        "max_duration_seconds": _as_pos_int(
            tmpl["max_duration_seconds"], 900, hard_cap=caps["max_duration_seconds"]
        ),
        "max_no_progress_repeats": _as_pos_int(
            tmpl["max_no_progress_repeats"],
            3,
            hard_cap=caps["max_no_progress_repeats"],
        ),
        "max_replans": _as_pos_int(
            tmpl["max_replans"], 2, hard_cap=caps["max_replans"]
        ),
        "used_tool_actions": 0,
        "used_retries": 0,
        "used_tokens": 0,
        "used_duration_ms": 0,
        "used_model_calls": 0,
        "reserved_tool_actions": 0,
    }

    scope = dict(auth_scope or {})
    if commission is not None and not scope.get("allowed_skills"):
        primary = getattr(commission, "primary_skills", None) or frozenset()
        aux = getattr(commission, "auxiliary_skills", None) or frozenset()
        scope.setdefault("allowed_skills", sorted(set(primary) | set(aux)))
        scope.setdefault("primary_skills", sorted(primary))
        scope.setdefault("auxiliary_skills", sorted(aux))
    effective["auth_scope"] = scope

    # 兼容旧字段别名
    effective["max_tool_steps"] = effective["max_tool_actions"]
    effective["max_retries"] = effective["max_attempts_per_step"]
    effective["max_duration_ms"] = effective["max_duration_seconds"] * 1000

    blob = json.dumps(
        {
            k: effective[k]
            for k in (
                "schema_version",
                "expected_business_steps",
                "max_tool_actions",
                "max_attempts_per_step",
                "max_model_calls",
                "max_tokens",
                "max_duration_seconds",
                "auth_scope",
            )
            if k in effective
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    effective["config_hash"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return effective


def budget_from_effective(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """写入 AgentTask.budget 的兼容形态。"""
    return {
        "schema_version": cfg.get("schema_version", SCHEMA_VERSION),
        "config_version": cfg.get("config_version", 1),
        "config_hash": cfg.get("config_hash", ""),
        "expected_business_steps": cfg.get("expected_business_steps", 0),
        "completed_business_steps": cfg.get("completed_business_steps", 0),
        "max_tool_steps": cfg.get("max_tool_actions", 30),
        "max_tool_actions": cfg.get("max_tool_actions", 30),
        "max_retries": cfg.get("max_attempts_per_step", 3),
        "max_attempts_per_step": cfg.get("max_attempts_per_step", 3),
        "max_tokens": cfg.get("max_tokens", 0),
        "max_duration_ms": cfg.get("max_duration_ms", 0),
        "max_duration_seconds": cfg.get("max_duration_seconds", 0),
        "max_model_calls": cfg.get("max_model_calls", 0),
        "max_no_progress_repeats": cfg.get("max_no_progress_repeats", 3),
        "max_replans": cfg.get("max_replans", 2),
        "response_locale": cfg.get("response_locale", "auto"),
        "used_tool_steps": cfg.get("used_tool_actions", 0),
        "used_tool_actions": cfg.get("used_tool_actions", 0),
        "used_retries": cfg.get("used_retries", 0),
        "used_tokens": cfg.get("used_tokens", 0),
        "used_duration_ms": cfg.get("used_duration_ms", 0),
        "used_model_calls": cfg.get("used_model_calls", 0),
        "reserved_tool_actions": cfg.get("reserved_tool_actions", 0),
    }


def validate_config_overrides(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """预检：非法值返回 error；通过返回裁剪后的 overrides。"""
    if not isinstance(overrides, dict):
        return {"ok": False, "error_code": "invalid_overrides", "message": "overrides 须为对象"}
    cleaned: Dict[str, Any] = {}
    for key, hard in SERVER_HARD_CAPS.items():
        if key not in overrides:
            continue
        raw = overrides[key]
        if isinstance(raw, bool) or raw is None:
            return {
                "ok": False,
                "error_code": "invalid_number",
                "message": f"{key} 必须为正整数",
            }
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error_code": "invalid_number",
                "message": f"{key} 必须为正整数",
            }
        if n <= 0:
            return {
                "ok": False,
                "error_code": "invalid_number",
                "message": f"{key} 必须为正整数",
            }
        if n > hard:
            cleaned[key] = hard
        else:
            cleaned[key] = n
    allowed_extra = {
        "allowed_skills",
        "allow_actions",
        "allowed_actions",
        "deny_actions",
        "denied_actions",
        "resource_scope",
        "upload_method_requirement",
        "response_locale",
        "expected_business_steps",
    }
    for list_key in (
        "allowed_skills",
        "allow_actions",
        "allowed_actions",
        "deny_actions",
        "denied_actions",
    ):
        if list_key not in overrides:
            continue
        raw_list = overrides[list_key]
        if isinstance(raw_list, str):
            items = [x.strip() for x in raw_list.split(",") if x.strip()]
        elif isinstance(raw_list, list):
            items = [str(x).strip() for x in raw_list if str(x).strip()]
        else:
            return {
                "ok": False,
                "error_code": "invalid_list",
                "message": f"{list_key} 须为字符串列表或逗号分隔字符串",
            }
        cleaned[list_key] = items[:50]
    if "expected_business_steps" in overrides:
        try:
            ebs = int(overrides["expected_business_steps"])
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error_code": "invalid_number",
                "message": "expected_business_steps 必须为正整数",
            }
        if ebs <= 0:
            return {
                "ok": False,
                "error_code": "invalid_number",
                "message": "expected_business_steps 必须为正整数",
            }
        cleaned["expected_business_steps"] = min(ebs, 100)
    unknown = [k for k in overrides if k not in SERVER_HARD_CAPS and k not in allowed_extra]
    if unknown:
        return {
            "ok": False,
            "error_code": "unknown_fields",
            "message": f"未知配置字段: {', '.join(unknown)}",
        }
    return {"ok": True, "overrides": cleaned}


def user_visible_config_summary(cfg: Dict[str, Any]) -> str:
    exp = int(cfg.get("expected_business_steps") or 0)
    done = int(cfg.get("completed_business_steps") or 0)
    used = int(cfg.get("used_tool_actions") or cfg.get("used_tool_steps") or 0)
    max_a = int(cfg.get("max_tool_actions") or cfg.get("max_tool_steps") or 0)
    return (
        f"本任务预计{exp}个业务检查点，最多{max_a}次工具调用。"
        f"当前已验收{done}/{exp}，工具已用{used}/{max_a}。"
    )


def apply_task_config(
    task,
    *,
    expected_version: int,
    overrides: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> Dict[str, Any]:
    """RC12：带 expected_version 的配置变更；冲突 409；不重置已用量。"""
    from django.db import transaction
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask

    overrides = overrides if isinstance(overrides, dict) else {}
    checked = validate_config_overrides(overrides)
    if not checked.get("ok"):
        return {
            "ok": False,
            "error_code": checked.get("error_code") or "invalid_overrides",
            "message": checked.get("message") or "invalid",
            "http_status": 400,
        }

    terminal = {"completed", "failed", "cancelled", "cancelling"}
    with transaction.atomic():
        row = AgentTask.objects.select_for_update().filter(pk=task.pk).first()
        if row is None:
            return {"ok": False, "error_code": "task_missing", "http_status": 404}
        if str(row.status or "") in terminal:
            return {
                "ok": False,
                "error_code": "task_terminal",
                "message": "终态任务不得以改配置复活",
                "http_status": 400,
            }
        budget = dict(row.budget or {})
        cur_ver = int(budget.get("config_version") or 1)
        try:
            expect = int(expected_version)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error_code": "invalid_expected_version",
                "message": "expected_version 必须为整数",
                "http_status": 400,
            }
        if expect != cur_ver:
            return {
                "ok": False,
                "error_code": "version_conflict",
                "message": f"config_version 冲突: expected={expect} current={cur_ver}",
                "current_version": cur_ver,
                "http_status": 409,
            }

        # 保留已用量与历史版本字段
        used_keys = (
            "used_tool_steps",
            "used_tool_actions",
            "used_retries",
            "used_tokens",
            "used_duration_ms",
            "used_model_calls",
            "completed_business_steps",
            "reserved_tool_actions",
            "over_budget",
            "over_budget_reason",
        )
        preserved = {k: budget.get(k) for k in used_keys if k in budget}

        ov = dict(checked.get("overrides") or {})
        ebs_override = ov.pop("expected_business_steps", None)
        # Skill/动作范围写入 auth_scope（不可抬高：与现有取交集/并集规则）
        auth_scope = dict(row.auth_scope or {})
        skills = ov.pop("allowed_skills", None)
        allow = ov.pop("allow_actions", None) or ov.pop("allowed_actions", None)
        deny = ov.pop("deny_actions", None) or ov.pop("denied_actions", None)
        if skills is not None:
            # 若已有允许列表，取交集，禁止抬权
            prev = set(auth_scope.get("allowed_skills") or [])
            nxt = set(skills)
            auth_scope["allowed_skills"] = sorted(prev & nxt) if prev else sorted(nxt)
        if allow is not None:
            prev = set(auth_scope.get("allow_actions") or [])
            nxt = set(allow)
            auth_scope["allow_actions"] = sorted(prev & nxt) if prev else sorted(nxt)
        if deny is not None:
            # deny 只增不减（更严）
            prev = set(auth_scope.get("deny_actions") or [])
            auth_scope["deny_actions"] = sorted(prev | set(deny))
        if ov.pop("resource_scope", None) is not None:
            auth_scope["resource_scope"] = overrides.get("resource_scope")

        effective = build_effective_config(
            stage_plan=list(row.stage_plan or []),
            auth_scope=auth_scope,
            overrides=ov,
            commission=None,
        )
        # 合并：新上限/策略 + 旧用量；bump config_version
        new_budget = budget_from_effective(effective)
        new_budget.update(preserved)
        new_budget["config_version"] = cur_ver + 1
        new_budget["last_config_reason"] = (reason or "")[:500]
        # 计划检查点变更 → bump plan_version，不覆盖已完成阶段事实
        plan_version = int(row.plan_version or 1)
        if ebs_override is not None:
            plan_version += 1
            new_budget["expected_business_steps"] = int(ebs_override)

        AgentTask.objects.filter(pk=row.pk).update(
            budget=new_budget,
            auth_scope=auth_scope,
            plan_version=plan_version,
            max_steps=int(
                new_budget.get("max_tool_actions")
                or new_budget.get("max_tool_steps")
                or row.max_steps
            ),
            updated_at=timezone.now(),
        )
        task.auth_scope = auth_scope
        task.budget = new_budget
        task.plan_version = plan_version
        try:
            from orchestrator_integration.agent_runtime import append_event

            append_event(
                task,
                "config_updated",
                status=row.status or "",
                payload={
                    "config_version": new_budget["config_version"],
                    "previous_version": cur_ver,
                    "reason": reason or "",
                    "overrides": checked.get("overrides") or {},
                },
            )
        except Exception:
            pass
        return {
            "ok": True,
            "config_version": new_budget["config_version"],
            "budget": new_budget,
            "summary": user_visible_config_summary(
                {
                    **new_budget,
                    "expected_business_steps": new_budget.get(
                        "expected_business_steps", 0
                    ),
                    "completed_business_steps": new_budget.get(
                        "completed_business_steps", 0
                    ),
                }
            ),
        }
