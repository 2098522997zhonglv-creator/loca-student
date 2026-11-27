"""编排公布的可用工具 ↔ Agent 实际绑定工具 一致性。

原则：
- 提示词只能声明「本轮已绑定」的工具名；禁止虚报未挂载工具。
- 编排一层要求的 builtin（尤其 execute_skill_script）若被旧意图裁剪卸掉，
  须在业务步骤开始前从工具池恢复挂载；仍缺则明确 blocked，勿要求用户重挂/重发。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

ORCH_REQUIRED_BUILTINS = frozenset(
    {
        "execute_skill_script",
        "read_skill_content",
        "request_executor_heal",
        "assert_task_postcondition",
        "report_task_blocked",
    }
)

_FALSE_UNMOUNTED_EXECUTE = re.compile(
    r"^[^\n]*未挂载\s*execute_skill_script[^\n]*$",
    re.M,
)
_ASK_USER_REROUTE = re.compile(
    r"^[^\n]*请明确请用户重说[^\n]*$",
    re.M,
)
_ZONE_NO_EXECUTE = re.compile(
    r"^[^\n]*本轮未挂载执行工具[^\n]*$",
    re.M,
)


def tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "").strip()


def mounted_tool_names(tools: Optional[Sequence[Any]]) -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()
    for t in tools or []:
        n = tool_name(t)
        if not n or n in seen:
            continue
        seen.add(n)
        names.append(n)
    return names


def orch_requires_execute(orch_mount: Any) -> bool:
    if orch_mount is None:
        return False
    builtins = set(getattr(orch_mount, "builtin_allow", None) or set())
    layer1 = list(
        getattr(getattr(orch_mount, "decision", None), "layer1_mount", None) or []
    )
    return ("execute_skill_script" in builtins) or (
        "execute_skill_script" in layer1
    )


def orch_required_builtin_names(orch_mount: Any) -> Set[str]:
    if orch_mount is None:
        return set()
    builtins = set(getattr(orch_mount, "builtin_allow", None) or set())
    return {n for n in builtins if n in ORCH_REQUIRED_BUILTINS}


def ensure_orchestrator_tools_bound(
    tools: Sequence[Any],
    *,
    orch_mount: Any = None,
    builtin_tool_pool: Optional[Sequence[Any]] = None,
    require_execute: Optional[bool] = None,
) -> Dict[str, Any]:
    """在业务步骤前：把编排要求但缺失的 builtin 从池中恢复挂载。

    返回：
      tools: 校正后的列表
      remounted: 本次补回的工具名
      missing: 池中也没有、仍缺的工具名
      ok: 必需工具是否齐
      require_execute: 本轮是否要求执行通道
    """
    out: List[Any] = list(tools or [])
    have = {tool_name(t) for t in out if tool_name(t)}
    pool_by_name = {
        tool_name(t): t for t in (builtin_tool_pool or []) if tool_name(t)
    }
    required = orch_required_builtin_names(orch_mount)
    need_exec = (
        bool(require_execute)
        if require_execute is not None
        else orch_requires_execute(orch_mount)
    )
    if need_exec:
        required.add("execute_skill_script")
        required.add("read_skill_content")

    remounted: List[str] = []
    missing: List[str] = []
    for name in sorted(required):
        if name in have:
            continue
        pool_tool = pool_by_name.get(name)
        if pool_tool is not None:
            out.append(pool_tool)
            have.add(name)
            remounted.append(name)
        else:
            missing.append(name)

    ok = "execute_skill_script" not in missing if need_exec else not missing
    # 非 execute 的 meta 缺失不单独判死（池可能未 include_meta），但 execute 必须齐
    if need_exec:
        ok = "execute_skill_script" in have

    return {
        "tools": out,
        "remounted": remounted,
        "missing": missing,
        "ok": ok,
        "require_execute": need_exec,
        "mounted": mounted_tool_names(out),
    }


def build_bound_tools_advertise_hint(
    mounted: Sequence[str],
    *,
    skill_allowlist: Optional[Iterable[str]] = None,
) -> str:
    """用实际绑定名生成公布文案（单一事实源）。"""
    names = [n for n in mounted if n]
    lines = [
        "# 本轮已绑定工具（与 Agent 工具列表一致）",
        f"- 可用工具: {', '.join(names) if names else '(无)'}",
        "- 禁止调用未列出的 tool 名；Skill 名称须经 execute_skill_script(skill_name=…)。",
        "- 禁止要求用户重新挂载工具或重新下发任务；缺能力由服务端恢复或明确 blocked。",
    ]
    skills = sorted({str(s).strip() for s in (skill_allowlist or []) if str(s).strip()})
    if skills:
        lines.append(f"- 本轮 Skill 白名单: {', '.join(skills)}")
    if "execute_skill_script" not in names:
        lines.append(
            "- 本轮未绑定 execute_skill_script：禁止编造落库/浏览器副作用；"
            "若编排需要执行通道，服务端应在开跑前恢复挂载或 blocked。"
        )
    return "\n".join(lines)


def patch_advertise_hint_for_bound_tools(
    hint: str,
    mounted: Sequence[str],
) -> str:
    """去掉与实际绑定矛盾的「未挂载 / 请用户重说」句。"""
    text = hint or ""
    have = set(mounted or [])
    if "execute_skill_script" in have:
        text = _FALSE_UNMOUNTED_EXECUTE.sub("", text)
        text = _ASK_USER_REROUTE.sub("", text)
        text = _ZONE_NO_EXECUTE.sub(
            "- 本轮已绑定执行工具 execute_skill_script；禁止要求用户重挂或重发任务。",
            text,
        )
    # 压缩多余空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def executor_gap_block_payload(
    *,
    missing: Sequence[str],
    remounted: Sequence[str],
) -> Dict[str, Any]:
    """无法自动恢复时的明确阻塞载荷（勿让模型/用户手动重挂）。"""
    miss = [m for m in missing if m]
    return {
        "ok": False,
        "blocker_code": "executor_bind_mismatch",
        "missing": miss,
        "remounted": list(remounted or []),
        "message": (
            "编排要求的执行工具未能绑定到 Agent："
            + (", ".join(miss) if miss else "execute_skill_script")
        ),
        "recovery": (
            "服务端已尝试自动恢复挂载但仍失败；请检查分区 allowlist / builtin 工具池，"
            "然后发送「继续」。禁止要求用户重新下发任务或手动重挂工具。"
        ),
        "reasons": [
            "编排公布的执行工具与 Agent 实际绑定不一致，且自动恢复失败："
            + (", ".join(miss) if miss else "execute_skill_script")
        ],
    }
