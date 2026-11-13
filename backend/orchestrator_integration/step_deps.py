"""步骤依赖与输出引用（FR02.2 / FR02.4）。

支持命令批次声明 step_id / depends_on / on_failure，
以及 ${step_id.field} 形式的输出引用。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REF_RE = re.compile(r"\$\{([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\}")


def extract_output_refs(text: str) -> List[Tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _REF_RE.finditer(text or "")]


def resolve_output_refs(
    text: str,
    outputs_by_step: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], str]:
    """用前置步骤结构化输出替换引用；来源未成功则拒绝。"""
    raw = text or ""
    refs = extract_output_refs(raw)
    if not refs:
        return None, raw

    def _lookup(step_id: str, field: str) -> Tuple[Optional[Any], Optional[str]]:
        """返回 (value, error)。D05/RC04：仅 succeeded 可引用；缺 status / unknown / failed / 不存在一律拒绝。"""
        if step_id not in outputs_by_step:
            return None, f"来源 step_id 不存在: {step_id}"
        payload = outputs_by_step.get(step_id) or {}
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"succeeded", "ok", "completed"}:
            reason = status or "missing_status"
            return None, f"来源 {step_id} 状态不可用({reason})"
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if field in data:
            return data[field], None
        if field in payload and field not in {"status", "data"}:
            return payload[field], None
        # a.b 嵌套
        cur: Any = data or payload
        for part in field.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None, f"来源 {step_id} 缺少字段 {field}"
            cur = cur[part]
        return cur, None

    missing: List[str] = []

    def _repl(m: re.Match) -> str:
        sid, field = m.group(1), m.group(2)
        val, err = _lookup(sid, field)
        if err or val is None:
            missing.append(err or f"{sid}.{field}")
            return m.group(0)
        return str(val)

    resolved = _REF_RE.sub(_repl, raw)
    if missing:
        return f"依赖输出缺失或未成功: {', '.join(missing)}", raw
    return None, resolved


def validate_batch_deps(commands: Sequence[dict]) -> Optional[str]:
    """校验依赖无环、引用存在；返回错误信息或 None。"""
    ids = []
    for c in commands:
        if not isinstance(c, dict):
            continue
        sid = str(c.get("step_id") or "").strip()
        if sid:
            ids.append(sid)
    seen = set()
    for sid in ids:
        if sid in seen:
            return f"重复 step_id: {sid}"
        seen.add(sid)

    id_set = set(ids)
    graph: Dict[str, List[str]] = {sid: [] for sid in id_set}
    for c in commands:
        if not isinstance(c, dict):
            continue
        sid = str(c.get("step_id") or "").strip()
        deps = [str(d) for d in (c.get("depends_on") or []) if d]
        for d in deps:
            if d not in id_set:
                return f"depends_on 引用不存在的 step_id: {d}"
            if sid:
                graph[sid].append(d)
        # 命令文本中的输出引用：本批声明了 step_id 时，引用必须落在已知集合（D05）
        for ref_sid, _field in extract_output_refs(str(c.get("command") or "")):
            if id_set and ref_sid not in id_set:
                return f"输出引用指向不存在的 step_id: {ref_sid}"

    # 环检测
    visiting: set = set()
    visited: set = set()

    def _dfs(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for nxt in graph.get(node) or []:
            if _dfs(nxt):
                return True
        visiting.discard(node)
        visited.add(node)
        return False

    for n in graph:
        if _dfs(n):
            return "depends_on 存在环依赖"

    return None


def apply_dependency_skips(
    commands: Sequence[dict],
    *,
    failed_step_ids: Optional[set] = None,
) -> List[dict]:
    """根据 depends_on / on_failure 标注 skipped（不修改已终态项）。"""
    failed = set(failed_step_ids or [])
    out: List[dict] = []
    for raw in commands:
        step = dict(raw) if isinstance(raw, dict) else {"command": raw}
        sid = str(step.get("step_id") or "")
        status = str(step.get("status") or "pending")
        if status in {"succeeded", "failed", "cancelled", "blocked", "skipped"}:
            out.append(step)
            if status in {"failed", "cancelled", "blocked"} and sid:
                failed.add(sid)
            continue
        deps = [str(d) for d in (step.get("depends_on") or []) if d]
        on_fail = str(step.get("on_failure") or "stop_dependents")
        blocked_by = [d for d in deps if d in failed]
        if blocked_by and on_fail == "stop_dependents":
            step["status"] = "skipped"
            step["skip_reason"] = f"dependency_failed:{','.join(blocked_by)}"
            if sid:
                failed.add(sid)
        out.append(step)
    return out
