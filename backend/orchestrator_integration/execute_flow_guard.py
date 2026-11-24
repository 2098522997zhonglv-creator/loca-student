"""用例执行区流程护栏：禁止「只查询不执行」与过早 degraded_draft。

app.log 核对：zone=execute / playbook=testcase_execute 多次只跑
get_testcases / get_testcase_detail / get_env / list_files，然后
report_task_blocked(degraded_draft)，全程无 execute_testcase / 有效浏览器实测。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

_DISCOVERY_RE = re.compile(
    r"(?i)--action\s+(?:"
    r"get_modules|get_testcases|get_testcase(?:_detail)?|get_ui_modules|"
    r"get_ui_pages|get_page_steps|get_env_configs|get_public_data|"
    r"get_actuators|get_execution_records?|list_files|list_testcases|"
    r"list_environments|get_module_tree"
    r")|"
    r"\b(?:get_modules|get_testcases|get_testcase_detail|get_env_configs|"
    r"list_files|get_execution_records)\b"
)

_RUN_RE = re.compile(
    r"(?i)(?:--action\s+)?(?:execute_testcase|run_testcase|batch_run_testcases|"
    r"run_interface(?:_case)?|execute_task_suite)\b|"
    r"node\s+run\.js|page\.goto\s*\(|chromium\.launch|"
    r"playwright-skill[^\n]{0,80}(?:goto|click|fill|screenshot)"
)

# 已有本次执行终态（含业务 failed）也算「跑完」，任务可收口（AC07）
_TERMINAL_RUN_RE = re.compile(
    r"(?i)(?:execution_id|record_id)\s*[:=]\S+.{0,120}"
    r"(?:终态|test_result|断言|passed|failed|error|skipped)|"
    r"终态\s*[:=]\s*(?:passed|failed|error|skipped)"
)

_CASE_EVIDENCE_RE = re.compile(
    r"(?i)\"case_id\"\s*:\s*\d+|\"testcase_id\"\s*:\s*\d+|"
    r"case_id\s*[:=]\s*\d+|testcase_id\s*[:=]\s*\d+|get_testcases"
)

FORCE_RUN_NUDGE = (
    "\n\n【编排强制推进】已查到可执行用例线索，但尚未真正执行。"
    "立刻二选一，禁止再堆 get_testcase_detail / list_files / get_env / "
    "get_execution_records / get_actuators：\n"
    "1) UI 自动化有用例：python ui_automation_tools.py --action execute_testcase "
    "--testcase_id <UiTestCase的id> --wait_result\n"
    "2) UI 用例为空：playwright-skill 按功能用例步骤对目标环境 goto 实测并截图；"
    "缺入口 URL 时用 knowledge_search 只查环境地址/账号，取到后立刻打开页面。\n"
    "loca-stude 的功能 case_id 不是 UiTestCase，禁止拿去 execute_testcase。\n"
    "在完成上述其一之前：禁止 report_task_blocked / assert_task_postcondition。"
)

PREMATURE_BLOCKED_MSG = (
    "拒绝过早 report_task_blocked：本轮已查询到用例/环境线索，但尚未调用 "
    "execute_testcase / run_testcase，也未用 playwright 做页面实测。"
    "请先执行（UI 有脚本则 execute_testcase --wait_result；否则 playwright "
    "按功能步骤实测），策略用尽后再 blocked。"
)


def _join_texts(texts: Optional[Sequence[Any]]) -> str:
    return "\n".join(str(x) for x in (texts or []) if x)


def analyze_execute_progress(
    tool_results: Optional[Sequence[Any]] = None,
    *,
    extra_commands: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """统计执行区进展：是否有用例证据、是否已真正跑起来、discovery 次数。"""
    parts: List[str] = [str(x) for x in (tool_results or []) if x]
    for cmd in extra_commands or []:
        if cmd:
            parts.append(str(cmd))
    joined = "\n".join(parts)
    discovery_hits = len(_DISCOVERY_RE.findall(joined))
    has_cases = bool(_CASE_EVIDENCE_RE.search(joined))
    has_run = bool(_RUN_RE.search(joined))
    return {
        "discovery_hits": discovery_hits,
        "has_cases": has_cases,
        "has_run": has_run,
        "joined_len": len(joined),
    }


def should_nudge_force_run(progress: Dict[str, Any]) -> bool:
    """查到用例后仍在 discovery 空转 → 需要强制推进提示。"""
    if not progress or progress.get("has_run"):
        return False
    if not progress.get("has_cases"):
        return False
    # 至少两轮查询类动作后强制推进（get_testcases + 再探环境/详情）
    return int(progress.get("discovery_hits") or 0) >= 2


def is_premature_blocked(progress: Dict[str, Any]) -> bool:
    """有用例线索但从未执行就想 degraded → 过早。"""
    if not progress:
        return False
    return bool(progress.get("has_cases")) and not bool(progress.get("has_run"))


def has_execute_run_evidence(tool_results: Optional[Sequence[Any]]) -> bool:
    joined = _join_texts(tool_results)
    if _RUN_RE.search(joined):
        return True
    # execution_id/record_id + 终态/断言：含业务失败的完整执行证据
    return bool(_TERMINAL_RUN_RE.search(joined))


def append_force_run_nudge(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    base = text if isinstance(text, str) else str(text)
    if "【编排强制推进】" in base:
        return base
    return base + FORCE_RUN_NUDGE
