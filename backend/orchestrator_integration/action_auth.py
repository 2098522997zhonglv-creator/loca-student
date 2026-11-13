"""动作授权与有界自愈（RC03 / RC14 / RC15）。

- RC14：Skill 可用 ≠ 任意动作；deny/allow 在外部副作用前求交。
- RC03：仅在授权且等价执行器组内切换；记录 reason/count。
- RC15：升预算只 bump config_version，不改 deny；扩权另走确认。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 动作别名 → 规范名
_ACTION_ALIASES = {
    "pay": "pay",
    "payment": "pay",
    "checkout_pay": "pay",
    "submit_order": "submit_order",
    "place_order": "submit_order",
    "create_order": "submit_order",
    "add_to_cart": "add_to_cart",
    "addtocart": "add_to_cart",
    "upload": "upload",
    "filechooser": "upload",
    "set_input_files": "upload",
    "login": "login",
    "navigate": "navigate",
    "goto": "navigate",
}

# 命令文本命中 → 推断动作（browser 自由脚本兜底）
_ACTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("pay", re.compile(r"(?i)\b(pay|payment|付款|支付|stripe\.confirm)\b")),
    (
        "submit_order",
        re.compile(
            r"(?i)\b(submit[_ ]?order|place[_ ]?order|create[_ ]?order|"
            r"提交订单|下单(?!车)|checkout(?![_\s-]*pay))\b"
        ),
    ),
    (
        "add_to_cart",
        re.compile(r"(?i)\b(add[_ ]?to[_ ]?cart|加购|加入购物车|cart\.add)\b"),
    ),
    (
        "upload",
        re.compile(
            r"(?i)\b(upload|filechooser|set_input_files|setInputFiles|传图|上传)\b"
        ),
    ),
    ("login", re.compile(r"(?i)\b(login|sign[_ ]?in|登录)\b")),
]

# RC03：等价浏览器执行器（同组内可有界切换）
EQUIVALENT_EXECUTOR_GROUPS: List[frozenset] = [
    frozenset({"playwright-skill", "playwright-cli"}),
    frozenset({"playwright-skill", "ui-automation"}),
]

# 上传方法等价（原生选择器 vs setInputFiles）
EQUIVALENT_UPLOAD_METHODS = frozenset({"filechooser", "set_input_files", "setInputFiles"})


def normalize_action(name: str) -> str:
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _ACTION_ALIASES.get(key, key)


def infer_actions_from_command(command: str) -> Set[str]:
    """从命令/脚本文本推断涉及的业务动作。"""
    text = command or ""
    found: Set[str] = set()
    for action, pat in _ACTION_PATTERNS:
        if pat.search(text):
            found.add(action)
    return found


def check_auth_actions(
    *,
    command: str = "",
    skill_name: str = "",
    auth_scope: Optional[Dict[str, Any]] = None,
    claimed_actions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """RC14：执行前动作授权求交。命中 deny 或超出 allow → 拒绝。

    规则：
    - deny 命中一律拒绝（即使 Skill 在白名单）
    - 若 allow 非空且推断到动作，动作须 ⊆ allow（或 allow 含通配 *）
    - allow/deny 皆空 → 放行（兼容未声明授权的旧任务）
    """
    scope = auth_scope if isinstance(auth_scope, dict) else {}
    allow = {normalize_action(a) for a in (scope.get("allow_actions") or []) if a}
    deny = {normalize_action(a) for a in (scope.get("deny_actions") or []) if a}
    inferred = set(claimed_actions or []) | infer_actions_from_command(command)
    inferred = {normalize_action(a) for a in inferred if a}

    if not inferred and not deny and not allow:
        return {"ok": True, "actions": [], "reason": "no_actions_inferred"}

    denied_hit = sorted(inferred & deny)
    if denied_hit:
        return {
            "ok": False,
            "error_code": "action_denied",
            "actions": sorted(inferred),
            "denied": denied_hit,
            "message": (
                f"动作 {denied_hit} 已被任务 deny，禁止执行"
                f"（skill={skill_name or '-'}）。Skill 可用不等于任意脚本获准。"
            ),
        }

    if allow and "*" not in allow and inferred:
        extra = sorted(inferred - allow)
        if extra:
            return {
                "ok": False,
                "error_code": "action_not_allowed",
                "actions": sorted(inferred),
                "not_allowed": extra,
                "allow_actions": sorted(allow),
                "message": (
                    f"动作 {extra} 不在 allow_actions={sorted(allow)} 内，"
                    f"禁止副作用（skill={skill_name or '-'}）。"
                ),
            }

    return {
        "ok": True,
        "actions": sorted(inferred),
        "allow_actions": sorted(allow),
        "deny_actions": sorted(deny),
    }


def resolve_equivalent_executor(
    *,
    current: str,
    candidates: List[str],
    allowed_skills: Set[str],
) -> Dict[str, Any]:
    """RC03：仅在同等价组且已授权 Skill 内切换。"""
    cur = (current or "").strip()
    allowed = {s.strip() for s in (allowed_skills or set()) if s}
    for cand in candidates or []:
        name = (cand or "").strip()
        if not name or name == cur:
            continue
        if name not in allowed:
            continue
        same_group = any(
            cur in group and name in group for group in EQUIVALENT_EXECUTOR_GROUPS
        )
        if same_group:
            return {
                "ok": True,
                "from": cur,
                "to": name,
                "reason": "equivalent_authorized_executor",
            }
    return {
        "ok": False,
        "from": cur,
        "error_code": "no_authorized_equivalent",
        "message": "无已授权的等价执行器可切换；禁止要求用户改路由话术。",
    }


def record_heal_switch(
    task,
    *,
    from_skill: str,
    to_skill: str,
    reason: str = "",
) -> Dict[str, Any]:
    """把有界自愈写入 task.facts，累计次数。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask

    facts = dict(getattr(task, "facts", None) or {})
    heals = list(facts.get("heal_switches") or [])
    entry = {
        "from": from_skill,
        "to": to_skill,
        "reason": (reason or "")[:300],
        "at": timezone.now().isoformat(),
    }
    heals.append(entry)
    facts["heal_switches"] = heals[-20:]
    facts["heal_switch_count"] = int(facts.get("heal_switch_count") or 0) + 1
    AgentTask.objects.filter(pk=task.pk).update(
        facts=facts, updated_at=timezone.now()
    )
    task.facts = facts
    return {
        "ok": True,
        "count": facts["heal_switch_count"],
        "entry": entry,
    }


def expand_budget_only(
    task,
    *,
    add_tool_actions: int = 0,
    reason: str = "",
) -> Dict[str, Any]:
    """RC15：仅增加工具额度并 bump config_version，不改 deny/allow。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask
    from orchestrator_integration.task_execution_config import SERVER_HARD_CAPS

    if add_tool_actions <= 0:
        return {"ok": False, "error_code": "invalid_delta", "message": "增量须为正"}
    budget = dict(task.budget or {})
    cur_max = int(
        budget.get("max_tool_actions") or budget.get("max_tool_steps") or 30
    )
    hard = int(SERVER_HARD_CAPS.get("max_tool_actions") or 200)
    new_max = min(cur_max + int(add_tool_actions), hard)
    if new_max <= cur_max:
        return {
            "ok": False,
            "error_code": "already_at_cap",
            "message": f"已达硬上限 {hard}",
            "max_tool_actions": cur_max,
        }
    old_ver = int(budget.get("config_version") or 1)
    budget["max_tool_actions"] = new_max
    budget["max_tool_steps"] = new_max
    budget["config_version"] = old_ver + 1
    budget["last_budget_expand"] = {
        "from": cur_max,
        "to": new_max,
        "reason": (reason or "")[:200],
        "at": timezone.now().isoformat(),
    }
    # 明确不碰 auth
    AgentTask.objects.filter(pk=task.pk).update(
        budget=budget,
        max_steps=new_max,
        updated_at=timezone.now(),
    )
    task.budget = budget
    task.max_steps = new_max
    return {
        "ok": True,
        "config_version": budget["config_version"],
        "max_tool_actions": new_max,
        "auth_unchanged": True,
        "deny_actions": list((task.auth_scope or {}).get("deny_actions") or []),
    }


def request_capability_expand(
    task,
    *,
    allow_actions: Optional[List[str]] = None,
    skill_name: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """RC15：扩权不静默生效，只登记 pending，须单独确认。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask

    facts = dict(getattr(task, "facts", None) or {})
    pending = {
        "allow_actions": [normalize_action(a) for a in (allow_actions or []) if a],
        "skill_name": (skill_name or "").strip(),
        "reason": (reason or "")[:300],
        "status": "pending_confirmation",
        "at": timezone.now().isoformat(),
    }
    if not pending["allow_actions"] and not pending["skill_name"]:
        return {
            "ok": False,
            "error_code": "empty_expand",
            "message": "须指定 allow_actions 或 skill_name",
        }
    facts["pending_capability_expand"] = pending
    AgentTask.objects.filter(pk=task.pk).update(
        facts=facts, updated_at=timezone.now()
    )
    task.facts = facts
    return {
        "ok": True,
        "status": "pending_confirmation",
        "pending": pending,
        "message": "扩权已登记，须用户单独确认后才写入 auth_scope；预算扩容不会附带本项。",
    }


def confirm_capability_expand(task, *, approve: bool) -> Dict[str, Any]:
    """确认或拒绝挂起的能力扩权。"""
    from django.utils import timezone

    from orchestrator_integration.models import AgentTask

    facts = dict(getattr(task, "facts", None) or {})
    pending = facts.get("pending_capability_expand")
    if not isinstance(pending, dict) or pending.get("status") != "pending_confirmation":
        return {"ok": False, "error_code": "no_pending_expand"}
    if not approve:
        pending["status"] = "rejected"
        facts["pending_capability_expand"] = pending
        AgentTask.objects.filter(pk=task.pk).update(
            facts=facts, updated_at=timezone.now()
        )
        return {"ok": True, "status": "rejected"}

    auth = dict(task.auth_scope or {})
    allow = set(auth.get("allow_actions") or [])
    deny = set(auth.get("deny_actions") or [])
    for a in pending.get("allow_actions") or []:
        na = normalize_action(a)
        if na and na not in deny:
            allow.add(na)
    auth["allow_actions"] = sorted(allow)
    # deny 不变
    skills = set(auth.get("allowed_skills") or [])
    if pending.get("skill_name"):
        skills.add(pending["skill_name"])
        auth["allowed_skills"] = sorted(skills)
    pending["status"] = "approved"
    facts["pending_capability_expand"] = pending
    budget = dict(task.budget or {})
    budget["config_version"] = int(budget.get("config_version") or 1) + 1
    AgentTask.objects.filter(pk=task.pk).update(
        auth_scope=auth,
        facts=facts,
        budget=budget,
        updated_at=timezone.now(),
    )
    task.auth_scope = auth
    task.facts = facts
    task.budget = budget
    return {
        "ok": True,
        "status": "approved",
        "auth_scope": auth,
        "config_version": budget["config_version"],
    }
