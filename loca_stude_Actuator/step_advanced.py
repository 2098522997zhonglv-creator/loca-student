"""P1 结构化步骤：自定义变量、条件判断、Python 不支持预检、前后置 SQL。"""

from __future__ import annotations

import json
import logging
import operator
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("actuator")

SUPPORTED_STEP_TYPES = frozenset({0, 1, 2, 3, 4})
# 5=Python：首期明确不支持，须提前报错而非静默当定位失败
UNSUPPORTED_STEP_TYPES = frozenset({5})

_OPS = {
    "eq": operator.eq,
    "==": operator.eq,
    "ne": operator.ne,
    "!=": operator.ne,
    "lt": operator.lt,
    "<": operator.lt,
    "le": operator.le,
    "<=": operator.le,
    "gt": operator.gt,
    ">": operator.gt,
    "ge": operator.ge,
    ">=": operator.ge,
    "contains": lambda a, b: str(b) in str(a),
    "in": lambda a, b: str(a) in str(b),
}


def preflight_unsupported_steps(steps: Sequence[Any]) -> Optional[str]:
    """执行前扫描；发现未实现类型则返回明确错误（禁止当定位失败）。"""
    bad = []
    for s in steps or []:
        st = int(getattr(s, "step_id", 0) or 0)
        typ = int(getattr(s, "step_type", 0) or 0)
        if typ in UNSUPPORTED_STEP_TYPES:
            bad.append(f"step_id={st} type={typ}(Python)")
        elif typ not in SUPPORTED_STEP_TYPES and typ not in UNSUPPORTED_STEP_TYPES:
            bad.append(f"step_id={st} type={typ}(未知)")
    if not bad:
        return None
    return (
        "不支持的步骤类型，已阻止执行（非定位失败）: "
        + "; ".join(bad)
        + "。请移除或改写后再跑；Python 步骤本期未实现。"
    )


def preflight_case_config(config: Any) -> Optional[str]:
    steps = []
    for ps in getattr(config, "page_steps", None) or []:
        steps.extend(getattr(ps, "steps", None) or [])
    return preflight_unsupported_steps(steps)


def _as_mapping(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
            return dict(data) if isinstance(data, dict) else {"value": data}
        except json.JSONDecodeError:
            return {"value": text}
    return {"value": raw}


def apply_custom_variables(processor: Any, custom: Any) -> Tuple[bool, str]:
    """step_type=3：写入本次运行变量（区别于项目公共变量持久化）。"""
    if processor is None:
        return False, "无变量处理器，无法设置自定义变量"
    data = _as_mapping(custom)
    if "set" in data and isinstance(data["set"], dict):
        data = dict(data["set"])
    if not data:
        return True, "自定义变量为空，已跳过"
    for k, v in data.items():
        key = str(k).strip()
        if not key:
            continue
        if processor and hasattr(processor, "replace"):
            v = processor.replace(v)
        processor.set_cache(key, v)
    return True, f"已设置运行变量: {', '.join(sorted(str(k) for k in data.keys()))}"


def _coerce(val: Any) -> Any:
    if isinstance(val, str):
        s = val.strip()
        if s.lower() in {"true", "yes"}:
            return True
        if s.lower() in {"false", "no"}:
            return False
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return val
    return val


def evaluate_condition(processor: Any, condition_value: Any) -> Tuple[bool, str]:
    """step_type=4：求值条件。

    支持:
    - {"left": "...", "op": "eq", "right": "..."}
    - {"expression": "${{flag}} == true"}
    - {"${{a}}": "1"} 简写为 eq
    """
    data = _as_mapping(condition_value)
    if not data:
        return True, "条件为空，默认 true"

    def _resolve(v: Any) -> Any:
        if processor and hasattr(processor, "replace"):
            return _coerce(processor.replace(v))
        return _coerce(v)

    if "expression" in data:
        expr = str(data.get("expression") or "")
        if processor and hasattr(processor, "replace"):
            expr = str(processor.replace(expr))
        m = re.match(
            r"^\s*(.+?)\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*$",
            expr,
        )
        if not m:
            truthy = expr.strip().lower() in {"1", "true", "yes", "ok"}
            return truthy, f"表达式「{expr}」按真值={truthy}"
        left, op, right = m.group(1), m.group(2), m.group(3)
        left, right = _coerce(left.strip(" \"'")), _coerce(right.strip(" \"'"))
        fn = _OPS.get(op)
        ok = bool(fn(left, right)) if fn else False
        return ok, f"{left!r} {op} {right!r} => {ok}"

    if "left" in data or "op" in data:
        left = _resolve(data.get("left"))
        right = _resolve(data.get("right"))
        op = str(data.get("op") or data.get("operator") or "eq").lower()
        fn = _OPS.get(op)
        if not fn:
            return False, f"不支持的条件运算符: {op}"
        ok = bool(fn(left, right))
        return ok, f"{left!r} {op} {right!r} => {ok}"

    # 简写：单键值对
    if len(data) == 1:
        k, v = next(iter(data.items()))
        left = _resolve(k) if "{{" in str(k) else _resolve(f"${{{{{k}}}}}") if processor else k
        # 若 key 不是变量语法，取 cache
        if processor and hasattr(processor, "get_cache") and "{{" not in str(k):
            left = processor.get_cache(str(k), left)
        right = _resolve(v)
        ok = left == right
        return ok, f"{left!r} == {right!r} => {ok}"

    return False, f"无法解析条件配置: {data!r}"


def parse_parametrize_rows(raw: Any) -> List[Dict[str, Any]]:
    if raw is None or raw == "" or raw == [] or raw == {}:
        return [{}]  # 单行空参数 = 跑一次
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [{}]
    if isinstance(raw, dict):
        # {"rows":[...]} or single row dict
        if "rows" in raw and isinstance(raw["rows"], list):
            rows = raw["rows"]
        else:
            rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        return [{}]
    out = []
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({"value": row})
    return out or [{}]


def apply_front_custom(processor: Any, front_custom: Any) -> Tuple[bool, str]:
    if front_custom in (None, "", [], {}):
        return True, "无前置变量"
    # 允许 list[{"key","value"}] 或 dict
    if isinstance(front_custom, list):
        mapping = {}
        for item in front_custom:
            if isinstance(item, dict) and item.get("key"):
                mapping[str(item["key"])] = item.get("value")
        return apply_custom_variables(processor, mapping)
    return apply_custom_variables(processor, front_custom)
