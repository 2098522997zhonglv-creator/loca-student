"""阶段 1 真实验收（本环境可复现）：对照需求验证方法 1–4。

口径：服务端状态闭环 + DB 持久化。不含远端 LLM / 真实商城 / Windows 部署实例。
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class Phase1AcceptanceReport(TestCase):
    """按阶段 1 验证方法逐项实测。"""

    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        # 打印可读验收表（manage.py test 捕获 stdout）
        print("\n===== 阶段1验收结果 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def _make_task(self, *, completed=1, stages=3, used=3, max_actions=80, status="running"):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        n = AgentTask.objects.count()
        user = User.objects.create_user(username=f"p1acc_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"p1-sess-{n}", title="phase1"
        )
        stage_plan = [
            {"stage_id": "query_cases"},
            {"stage_id": "run_or_browser"},
            {"stage_id": "assert_and_deliver"},
        ][:stages]
        return AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id=f"p1{n:08d}",
            goal="需要 3 个业务检查点的回归任务",
            status=status,
            current_stage=stage_plan[min(completed, len(stage_plan) - 1)]["stage_id"],
            stage_plan=stage_plan,
            budget={
                "expected_business_steps": stages,
                "completed_business_steps": completed,
                "used_tool_actions": used,
                "max_tool_actions": max_actions,
            },
            facts={
                "completed_stage_ids": [s["stage_id"] for s in stage_plan[:completed]]
            },
        )

    def _reload(self, task_id: int):
        """模拟刷新页面：重新读库 + 进度投影。"""
        from orchestrator_integration.agent_runtime import task_snapshot
        from orchestrator_integration.models import AgentTask, TaskEvent
        from orchestrator_integration.runtime_progress import progress_event

        task = AgentTask.objects.get(pk=task_id)
        snap = task_snapshot(task)
        prog = progress_event(task.pk)
        events = list(
            TaskEvent.objects.filter(task=task)
            .order_by("sequence")
            .values("event_type", "status", "payload")
        )
        return task, snap, prog, events

    # --- 验证方法 1：3 检查点，第 1 步后提前终答 ---
    def test_v1_early_final_cannot_hang_running(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=1, stages=3)
        # 模型声称完成（acceptance ok），但只完成 1/3
        result = close_task_after_stream(
            task,
            acceptance={
                "ok": True,
                "reasons": ["模型提前输出最终回复"],
                "post_condition": "done",
            },
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        task, snap, prog, events = self._reload(task.pk)

        hung = task.status == "running"
        has_reason = bool(task.error_message) or bool(
            (task.acceptance or {}).get("reasons")
        )
        has_event = any(
            e["event_type"] in {"task_blocked", "stream_closure"} for e in events
        )
        has_recovery = bool(result.get("recovery")) or any(
            (b or {}).get("recovery")
            for b in ((task.acceptance or {}).get("blockers") or [])
        )
        not_success = task.status != "completed" and not (task.acceptance or {}).get(
            "ok"
        )
        passed = (
            (not hung)
            and result.get("status") == "blocked"
            and task.status == "blocked"
            and has_reason
            and has_event
            and has_recovery
            and not_success
        )
        self._record(
            "V1_early_final",
            passed,
            f"status={task.status} result={result.get('status')} "
            f"blocker={result.get('blocker_code')} recovery={bool(has_recovery)} "
            f"events={[e['event_type'] for e in events]} "
            f"snap_status={snap.get('status')}",
        )

    # --- 验证方法 2a：用户停止 ---
    def test_v2_user_stop(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task()
        result = close_task_after_stream(task, user_stopped=True)
        task, _, _, events = self._reload(task.pk)
        passed = task.status == "cancelled" and result["status"] == "cancelled"
        self._record(
            "V2_user_stop",
            passed,
            f"status={task.status} events={[e['event_type'] for e in events[-3:]]}",
        )

    # --- 验证方法 2b：审批等待 ---
    def test_v2_waiting_approval(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task()
        result = close_task_after_stream(task, interrupt_detected=True)
        task, _, _, _ = self._reload(task.pk)
        passed = (
            result["status"] == "waiting_approval"
            and task.status == "awaiting_approval"
        )
        self._record(
            "V2_waiting_approval",
            passed,
            f"api_status={result['status']} db_status={task.status}",
        )

    # --- 验证方法 2c：工具异常 ---
    def test_v2_tool_error(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=0)
        result = close_task_after_stream(task, tool_error=True)
        task, _, _, _ = self._reload(task.pk)
        passed = (
            task.status == "blocked"
            and result.get("blocker_code") == "tool_error_incomplete"
        )
        self._record(
            "V2_tool_error",
            passed,
            f"status={task.status} code={result.get('blocker_code')}",
        )

    # --- 验证方法 2d：预算耗尽 ---
    def test_v2_budget_exhausted(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=1, used=80, max_actions=80)
        result = close_task_after_stream(task)
        task, _, _, _ = self._reload(task.pk)
        passed = (
            task.status == "blocked"
            and result.get("blocker_code") == "budget_exhausted"
        )
        self._record(
            "V2_budget_exhausted",
            passed,
            f"status={task.status} code={result.get('blocker_code')}",
        )

    # --- 验证方法 3：刷新后与 DB 一致 ---
    def test_v3_refresh_matches_db(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=1)
        close_task_after_stream(
            task,
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        # 第一次读
        t1, snap1, prog1, ev1 = self._reload(task.pk)
        # 「刷新」再读
        t2, snap2, prog2, ev2 = self._reload(task.pk)

        status_same = t1.status == t2.status == "blocked"
        snap_status_same = (snap1.get("status") or t1.status) == (
            snap2.get("status") or t2.status
        )
        event_count_same = len(ev1) == len(ev2) and len(ev1) > 0
        # 进度事件可序列化且带 task 标识
        prog_ok = isinstance(prog2, dict) and (
            prog2.get("task_id")
            or prog2.get("type")
            or "budget" in prog2
            or "status" in prog2
        )
        incomplete_not_success = t2.status != "completed"
        passed = (
            status_same
            and snap_status_same
            and event_count_same
            and prog_ok
            and incomplete_not_success
        )
        self._record(
            "V3_refresh_persistence",
            passed,
            f"db={t2.status} snap={snap2.get('status')} events={len(ev2)} "
            f"prog_keys={list(prog2)[:8] if isinstance(prog2, dict) else prog2}",
        )

    # --- 通过标准汇总：无悬挂、无无原因、未完成不显示成功 ---
    def test_v4_pass_criteria_matrix(self):
        from orchestrator_integration.task_state_closure import (
            close_task_after_stream,
            reconcile_stale_running_tasks,
        )

        # 陈旧 running 回收
        stale = self._make_task(completed=0)
        from orchestrator_integration.models import AgentTask

        AgentTask.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - timedelta(seconds=2000),
            lease_expires_at=timezone.now() - timedelta(seconds=100),
        )
        out = reconcile_stale_running_tasks(older_than_seconds=900)
        stale.refresh_from_db()
        stale_ok = stale.status == "blocked" and out["count"] >= 1

        # 全检查点完成才允许 completed
        done = self._make_task(completed=3, stages=3)
        r = close_task_after_stream(
            done, acceptance={"ok": True, "reasons": [], "evidence": True}
        )
        done.refresh_from_db()
        complete_ok = done.status == "completed" and r["status"] == "completed"

        passed = stale_ok and complete_ok
        self._record(
            "V4_pass_criteria",
            passed,
            f"stale_ok={stale_ok} complete_ok={complete_ok} reconcile={out}",
        )
