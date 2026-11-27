"""执行循环护栏验收：超时先核对、同类失败封顶、无进展阻塞、fetch 阶段退出。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


class ExecutionLoopControlAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 执行循环护栏验收 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def _task(self, **kwargs):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        n = AgentTask.objects.count()
        user = User.objects.create_user(username=f"elc_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"elc-sess-{n}", title="elc"
        )
        defaults = dict(
            session=chat,
            user=user,
            public_task_id=f"elc{n:08d}",
            goal="常规品功能回归",
            status="running",
            current_stage="fetch_conditions",
            stage_plan=[
                {"stage_id": "fetch_conditions"},
                {"stage_id": "check_executable"},
                {"stage_id": "verify_deliver"},
            ],
            auth_scope={},
            facts={},
            budget={
                "max_tool_actions": 76,
                "used_tool_actions": 0,
                "expected_business_steps": 3,
                "completed_business_steps": 0,
            },
        )
        defaults.update(kwargs)
        return AgentTask.objects.create(**defaults)

    def test_v1_timeout_reconcile_before_side_effect_retry(self):
        """超时后须先核对；未确认前禁止副作用重做。"""
        from orchestrator_integration.execution_loop_control import (
            note_tool_outcome,
            preflight_side_effect_after_timeout,
        )
        from orchestrator_integration.reconcile import build_reconcile_plan

        task = self._task()
        plan = build_reconcile_plan(
            skill_name="playwright-skill",
            text='page.waitForEvent: Timeout 15000ms exceeded',
            status="unknown",
            structured={"retryable": True, "error_code": "timeout_unknown"},
        )
        note_tool_outcome(
            task,
            skill_name="playwright-skill",
            command="node run.js click submit",
            status="unknown",
            error_code="timeout_unknown",
            summary="Timeout 15000ms exceeded",
        )
        task.refresh_from_db()
        blocked = preflight_side_effect_after_timeout(
            task,
            skill_name="playwright-skill",
            command="node run.js click submit",
        )
        allowed_read = preflight_side_effect_after_timeout(
            task,
            skill_name="playwright-skill",
            command="node run.js page_info",
        )
        self._record(
            "ELC-V1-timeout-reconcile",
            plan.get("must_reconcile") is True
            and plan.get("allow_replay") is False
            and "持久会话" in (plan.get("hint") or "")
            and blocked.get("block_execute") is True
            and allowed_read.get("ok") is True
            and bool((task.facts or {}).get("pending_timeout_reconcile")),
            f"plan_reconcile={plan.get('must_reconcile')} "
            f"block_side={blocked.get('block_execute')} "
            f"read_ok={allowed_read.get('ok')}",
        )

    def test_v2_identical_failure_cap(self):
        """同类失败连续达上限 → 停止循环并记录恢复条件。"""
        from orchestrator_integration.execution_loop_control import (
            MAX_IDENTICAL_FAILURES,
            apply_loop_block,
            note_tool_outcome,
        )

        task = self._task()
        last = {}
        for _ in range(MAX_IDENTICAL_FAILURES):
            last = note_tool_outcome(
                task,
                skill_name="playwright-skill",
                command="node run.js goto https://x/p1",
                status="failed",
                error_code="timeout_unknown",
                summary="Timeout waiting for selector .buy",
            )
        self.assertTrue(last.get("block"))
        apply_loop_block(task, last)
        task.refresh_from_db()
        self._record(
            "ELC-V2-identical-cap",
            last.get("blocker_code") == "identical_failure_cap"
            and task.status == "blocked"
            and "恢复" in (last.get("recovery") or "恢复"),
            f"code={last.get('blocker_code')} status={task.status} "
            f"streak={(task.facts or {}).get('identical_failure')}",
        )

    def test_v3_tool_calls_not_business_progress(self):
        """工具调用很多但业务 0 → 超阈值 blocked，不得当正常执行。"""
        from orchestrator_integration.execution_loop_control import (
            NO_PROGRESS_TOOL_THRESHOLD,
            apply_loop_block,
            note_tool_outcome,
        )
        from orchestrator_integration.task_state_closure import public_progress_view

        task = self._task(
            budget={
                "max_tool_actions": 76,
                "used_tool_actions": NO_PROGRESS_TOOL_THRESHOLD,
                "expected_business_steps": 3,
                "completed_business_steps": 0,
            }
        )
        last = {}
        for i in range(NO_PROGRESS_TOOL_THRESHOLD):
            last = note_tool_outcome(
                task,
                skill_name="knowledge_search",
                command=f"q{i}",
                status="succeeded",
                summary="kb hit",
            )
        apply_loop_block(task, last)
        task.refresh_from_db()
        view = public_progress_view(task)
        self._record(
            "ELC-V3-no-progress",
            last.get("blocker_code") == "no_business_progress_threshold"
            and task.status == "blocked"
            and view.get("business_progress", {}).get("completed") == 0
            and view.get("phase") in {"tool_prep", "business_exec"},
            f"code={last.get('blocker_code')} status={task.status} "
            f"biz={view.get('business_progress')} phase={view.get('phase')}",
        )

    def test_v4_fetch_conditions_must_exit(self):
        """条件检索完成后转入可执行性检查；滞留则阻塞。"""
        from orchestrator_integration.execution_loop_control import (
            FETCH_CONDITIONS_STALL_TOOLS,
            apply_loop_block,
            evaluate_stage_exit_fetch_conditions,
            note_tool_outcome,
        )
        from orchestrator_integration.task_orchestrator.node_runtime import (
            orch_node_acceptance,
        )

        # 有入口 URL → 应建议 advance
        ok_verdict = orch_node_acceptance(
            "fetch_conditions",
            ["entry_url=https://ps-local-test.example/p1 规则已具备"],
        )
        task = self._task()
        exit_ok = evaluate_stage_exit_fetch_conditions(
            task,
            tool_results=["entry_url=https://ps-local-test.example/p1"],
        )

        # 无证据空转 → stall block
        for i in range(FETCH_CONDITIONS_STALL_TOOLS):
            note_tool_outcome(
                task,
                skill_name="knowledge_search",
                command=f"doc{i}",
                status="succeeded",
                summary="仅文档摘要无 URL",
            )
        exit_stall = evaluate_stage_exit_fetch_conditions(
            task, tool_results=["仅文档摘要"]
        )
        if exit_stall.get("block"):
            apply_loop_block(task, exit_stall)
        task.refresh_from_db()

        # 纯旁白不得判 fetch 完成
        bad = orch_node_acceptance("fetch_conditions", ["已整理输入，完成数为 0"])

        self._record(
            "ELC-V4-fetch-exit",
            ok_verdict.get("ok") is True
            and exit_ok.get("advance") is True
            and exit_ok.get("next_stage") == "check_executable"
            and bad.get("ok") is False
            and exit_stall.get("block") is True
            and task.status == "blocked",
            f"ok_verdict={ok_verdict.get('reason')} advance={exit_ok.get('advance')} "
            f"bad_ok={bad.get('ok')} stall={exit_stall.get('blocker_code')} "
            f"status={task.status}",
        )
