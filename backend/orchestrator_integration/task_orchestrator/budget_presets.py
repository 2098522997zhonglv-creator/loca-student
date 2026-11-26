"""任务编排步数预设：一层预设 / 二层备用 / 三层批准（与工具预算分离又共同约束）。

预计业务节点 ≠ 工具调用上限。执行测试用例路径默认拉长工具预算，
并按用例条数线性放大（受服务端硬上限约束）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence

from orchestrator_integration.task_execution_config import SERVER_HARD_CAPS

PLAN_KIND_TESTCASE = "testcase_execute"
PLAN_KIND_BROWSER = "browser_act"
PLAN_KIND_PLATFORM = "platform"
PLAN_KIND_SIMPLE = "simple"

_CASE_RANGE_RE = re.compile(r"(?<!\d)(\d{2,5})\s*[-~—－至到]\s*(\d{2,5})(?!\d)")
_CASE_SINGLE_RE = re.compile(
    r"(?i)(?:用例|case|id|testcase|#)[^\d]{0,8}(\d{2,5})"
    r"|(?<!\d)(\d{3,5})(?!\d)"
)


@dataclass(frozen=True)
class LayerBudgetPreset:
    """三层工具步数预设（实际调用次数上限分量）。"""

    plan_kind: str
    layer1_preset: int
    """一层：当前节点直接执行已授权操作的预算分量。"""
    layer2_reserve: int
    """二层：补条件 / 主工具失败 / 等价备用自动启用时追加。"""
    layer3_reserve: int
    """三层：用户批准超授权动作后续跑预留。"""
    estimated_nodes: int = 0
    case_count: int = 0
    max_tool_actions: int = 0
    max_tokens: int = 50_000
    max_duration_seconds: int = 900
    max_model_calls: int = 30

    def total_tool_actions(self) -> int:
        hard = int(SERVER_HARD_CAPS.get("max_tool_actions") or 1000)
        raw = self.max_tool_actions or (
            self.layer1_preset + self.layer2_reserve + self.layer3_reserve
        )
        return min(max(1, int(raw)), hard)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["max_tool_actions"] = self.total_tool_actions()
        d["layers"] = {
            "1_current": self.layer1_preset,
            "2_fallback": self.layer2_reserve,
            "3_pending_auth": self.layer3_reserve,
        }
        return d

    def as_config_overrides(self) -> Dict[str, Any]:
        return {
            "max_tool_actions": self.total_tool_actions(),
            "max_tokens": min(
                int(self.max_tokens),
                int(SERVER_HARD_CAPS.get("max_tokens") or 2_000_000),
            ),
            "max_duration_seconds": min(
                int(self.max_duration_seconds),
                int(SERVER_HARD_CAPS.get("max_duration_seconds") or 14_400),
            ),
            "max_model_calls": min(
                int(self.max_model_calls),
                int(SERVER_HARD_CAPS.get("max_model_calls") or 400),
            ),
        }


# 基线预设（单条/短任务）；执行用例默认显著高于浏览器加购
_BASE_PRESETS: Dict[str, Dict[str, int]] = {
    PLAN_KIND_TESTCASE: {
        "layer1_preset": 48,
        "layer2_reserve": 32,
        "layer3_reserve": 16,
        "max_tokens": 120_000,
        "max_duration_seconds": 2400,
        "max_model_calls": 40,
        "per_case_tokens": 20_000,
        "per_case_model_calls": 4,
        "per_case_duration_seconds": 60,
        "per_case_layer1": 12,
        "per_case_layer2": 8,
    },
    PLAN_KIND_BROWSER: {
        "layer1_preset": 40,
        "layer2_reserve": 24,
        "layer3_reserve": 12,
        "max_tokens": 120_000,
        "max_duration_seconds": 1800,
        "max_model_calls": 40,
        "per_node_tokens": 30_000,
        "per_node_model_calls": 8,
        "per_node_duration_seconds": 300,
        "per_case_layer1": 0,
        "per_case_layer2": 0,
    },
    PLAN_KIND_PLATFORM: {
        "layer1_preset": 12,
        "layer2_reserve": 8,
        "layer3_reserve": 8,
        "max_tokens": 50_000,
        "max_duration_seconds": 900,
        "max_model_calls": 16,
        "per_case_layer1": 0,
        "per_case_layer2": 0,
    },
    PLAN_KIND_SIMPLE: {
        "layer1_preset": 10,
        "layer2_reserve": 6,
        "layer3_reserve": 4,
        "max_tokens": 40_000,
        "max_duration_seconds": 600,
        "max_model_calls": 12,
        "per_case_layer1": 0,
        "per_case_layer2": 0,
    },
}


def infer_plan_kind(
    *,
    node_ids: Sequence[str] = (),
    playbook_id: str = "",
    message: str = "",
) -> str:
    ids = set(node_ids or [])
    pb = (playbook_id or "").strip()
    text = message or ""
    if (
        pb == PLAN_KIND_TESTCASE
        or "query_cases" in ids
        or "run_or_browser" in ids
        or any(k in text for k in ("执行用例", "执行测试", "跑用例", "testcase"))
        or (
            "回归" in text
            and any(cue in text for cue in ("测试", "功能", "用例", "常规品", "模块", "商品"))
            and re.search(r"执行|开始|运行|跑|验证|复测|开展|帮我|请你", text)
        )
        or (
            ("执行" in text or "运行" in text or "跑" in text)
            and ("用例" in text or "模块" in text)
        )
    ):
        return PLAN_KIND_TESTCASE
    if (
        pb == PLAN_KIND_BROWSER
        or "add_to_cart" in ids
        or "customize_upload" in ids
        or "check_executable" in ids
    ):
        return PLAN_KIND_BROWSER
    if "platform_draft" in ids or pb.startswith("platform"):
        return PLAN_KIND_PLATFORM
    return PLAN_KIND_SIMPLE


def count_testcase_refs(message: str) -> int:
    """估算消息中点名的用例条数（区间按闭区间计数，上限 80）。"""
    text = message or ""
    ids: set = set()
    covered_spans = []
    for m in _CASE_RANGE_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        if 0 <= hi - lo <= 80:
            ids.update(range(lo, hi + 1))
        else:
            ids.add(lo)
            ids.add(hi)
        covered_spans.append(m.span())

    def _in_covered(pos: int) -> bool:
        return any(s <= pos < e for s, e in covered_spans)

    for m in _CASE_SINGLE_RE.finditer(text):
        if _in_covered(m.start()):
            continue
        for g in m.groups():
            if g and g.isdigit():
                ids.add(int(g))
    # 过滤明显不是用例号的年份等
    filtered = {i for i in ids if 1 <= i <= 99999 and not (1900 <= i <= 2100)}
    return min(len(filtered), 80) if filtered else 0


def build_layer_budget_preset(
    *,
    plan_kind: str,
    estimated_nodes: int = 0,
    message: str = "",
    case_count: Optional[int] = None,
) -> LayerBudgetPreset:
    kind = plan_kind if plan_kind in _BASE_PRESETS else PLAN_KIND_SIMPLE
    base = dict(_BASE_PRESETS[kind])
    n_cases = case_count if case_count is not None else count_testcase_refs(message)
    # 至少按 1 条计放大；未点名 ID 的「执行分支全部用例」给执行类默认放大系数
    scale_cases = n_cases
    if kind == PLAN_KIND_TESTCASE and scale_cases <= 0:
        # 未点名具体 ID：按「一批」预留（约 8 条量级），避免仍用 4 步短路预算
        scale_cases = 8
    extra = max(0, scale_cases - 1)
    l1 = int(base["layer1_preset"]) + extra * int(base.get("per_case_layer1") or 0)
    l2 = int(base["layer2_reserve"]) + extra * int(base.get("per_case_layer2") or 0)
    l3 = int(base["layer3_reserve"])
    # 多阶段浏览器流程需要按业务节点放大，不再只给几个额外调用。
    node_extra = max(0, int(estimated_nodes) - 3)
    node_bonus = min(160, node_extra * 12)
    l1 += node_bonus
    hard = int(SERVER_HARD_CAPS.get("max_tool_actions") or 1000)
    total = min(l1 + l2 + l3, hard)
    # 若被硬上限裁切，按比例收缩但保留三层非零
    if l1 + l2 + l3 > hard:
        l3 = max(4, min(l3, hard // 8))
        l2 = max(8, min(l2, (hard - l3) // 3))
        l1 = max(8, hard - l2 - l3)
        total = l1 + l2 + l3
    workload_extra = (
        max(0, int(scale_cases) - 1)
        if kind == PLAN_KIND_TESTCASE
        else node_extra if kind == PLAN_KIND_BROWSER else 0
    )
    workload_unit = "case" if kind == PLAN_KIND_TESTCASE else "node"
    max_tokens = min(
        int(base["max_tokens"])
        + workload_extra * int(base.get(f"per_{workload_unit}_tokens") or 0),
        int(SERVER_HARD_CAPS.get("max_tokens") or 2_000_000),
    )
    max_model_calls = min(
        int(base["max_model_calls"])
        + workload_extra * int(base.get(f"per_{workload_unit}_model_calls") or 0),
        int(SERVER_HARD_CAPS.get("max_model_calls") or 400),
    )
    max_duration_seconds = min(
        int(base["max_duration_seconds"])
        + workload_extra * int(base.get(f"per_{workload_unit}_duration_seconds") or 0),
        int(SERVER_HARD_CAPS.get("max_duration_seconds") or 14_400),
    )
    return LayerBudgetPreset(
        plan_kind=kind,
        layer1_preset=l1,
        layer2_reserve=l2,
        layer3_reserve=l3,
        estimated_nodes=int(estimated_nodes or 0),
        case_count=int(n_cases or scale_cases if kind == PLAN_KIND_TESTCASE else n_cases),
        max_tool_actions=total,
        max_tokens=max_tokens,
        max_duration_seconds=max_duration_seconds,
        max_model_calls=max_model_calls,
    )


def topup_add_for_plan_kind(plan_kind: str) -> int:
    """用户续跑/补充时自动续额的默认增量。"""
    if plan_kind == PLAN_KIND_TESTCASE:
        return 48
    if plan_kind == PLAN_KIND_BROWSER:
        return 28
    return 12
