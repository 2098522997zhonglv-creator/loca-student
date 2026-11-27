"""阶段 1 真实验收（可复现）：对照核验矩阵。

口径：服务端状态闭环 + DB/事件/进度投影一致。
覆盖：提前终答、停止、审批、工具异常、预算耗尽（含 0/N 无进展）、刷新恢复。
不含远端 LLM / 真实商城 / Windows 部署实例。
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
        print("\n===== 阶段1验收结果 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def _make_task(
        self,
        *,
        completed=1,
        stages=3,
        used=3,
        max_actions=80,
        status="running",
    ):
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
        from orchestrator_integration.task_state_closure import public_progress_view

        task = AgentTask.objects.get(pk=task_id)
        snap = task_snapshot(task)
        prog = progress_event(task.pk)
        view = public_progress_view(task)
        events = list(
            TaskEvent.objects.filter(task=task)
            .order_by("sequence")
            .values("event_type", "status", "payload")
        )
        return task, snap, prog, events, view

    # --- 验证方法 1：3 检查点，第 1 步后提前终答 ---
    def test_v1_early_final_auto_continue_or_blocked(self):
        """受控复现：完成第 1 步后模型提前终答 → 不得假完成；续跑失败则明确 blocked。"""
        from orchestrator_integration.final_response_policy import (
            looks_like_early_final_answer,
            should_attempt_checkpoint_continue,
        )
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=1, stages=3, used=10, max_actions=80)
        early_text = "任务已完成，最终结果如下，可以结束了。"
        self.assertTrue(looks_like_early_final_answer(early_text))
        should_cont = should_attempt_checkpoint_continue(
            response_text=early_text,
            executor_mounted=True,
            user_stopped=False,
            interrupt_detected=False,
            used_tool_actions=10,
            reserved_tool_actions=0,
            max_tool_actions=80,
            completed_business_steps=1,
            expected_business_steps=3,
            acceptance_ok=True,
        )
        # 同轮应尝试续跑；若续跑未推进检查点 → blocked
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
        task, snap, prog, events, view = self._reload(task.pk)

        hung = task.status == "running"
        fake_success = task.status == "completed" or view.get("success_visible")
        has_recovery = bool(result.get("recovery")) or any(
            (b or {}).get("recovery")
            for b in ((task.acceptance or {}).get("blockers") or [])
        )
        has_event = any(
            e["event_type"] in {"task_blocked", "stream_closure"} for e in events
        )
        passed = (
            should_cont
            and (not hung)
            and (not fake_success)
            and result.get("status") == "blocked"
            and task.status == "blocked"
            and view.get("label") == "未完全完成"
            and view["business_progress"]["text"] == "1/3"
            and has_recovery
            and has_event
        )
        self._record(
            "V1_early_final",
            passed,
            f"should_cont={should_cont} status={task.status} "
            f"blocker={result.get('blocker_code')} label={view.get('label')} "
            f"progress={view['business_progress']['text']} "
            f"snap={snap.get('status')} events={[e['event_type'] for e in events]}",
        )

    # --- 验证方法 2a：用户停止 ---
    def test_v2_user_stop(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task()
        result = close_task_after_stream(task, user_stopped=True)
        task, _, _, events, view = self._reload(task.pk)
        passed = (
            task.status == "cancelled"
            and result["status"] == "cancelled"
            and not view.get("success_visible")
        )
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
        task, _, _, _, view = self._reload(task.pk)
        passed = (
            result["status"] == "waiting_approval"
            and task.status == "awaiting_approval"
            and not view.get("success_visible")
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
        task, _, _, _, view = self._reload(task.pk)
        passed = (
            task.status == "blocked"
            and result.get("blocker_code") == "tool_error_incomplete"
            and view.get("label") == "未完全完成"
        )
        self._record(
            "V2_tool_error",
            passed,
            f"status={task.status} code={result.get('blocker_code')} "
            f"label={view.get('label')}",
        )

    # --- 验证方法 2d：预算耗尽（有部分业务进展） ---
    def test_v2_budget_exhausted(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=1, used=80, max_actions=80)
        result = close_task_after_stream(task)
        task, _, _, _, view = self._reload(task.pk)
        passed = (
            task.status == "blocked"
            and result.get("blocker_code") == "budget_exhausted"
            and view["tool_budget"]["text"] == "80/80"
            and view["business_progress"]["text"] == "1/3"
        )
        self._record(
            "V2_budget_exhausted",
            passed,
            f"status={task.status} code={result.get('blocker_code')} "
            f"tools={view['tool_budget']['text']} biz={view['business_progress']['text']}",
        )

    # --- 真实失败形态：0/3 + 76/76 ---
    def test_v2e_budget_burn_zero_business_progress(self):
        """真实记录形态：工具 76/76、业务 0/3 → 明确 blocked，不得显示成功。"""
        from orchestrator_integration.task_state_closure import (
            close_task_after_stream,
            public_progress_view,
        )

        task = self._make_task(completed=0, stages=3, used=76, max_actions=76)
        result = close_task_after_stream(
            task,
            tool_error=True,  # 页面超时/选择器超时/浏览器异常
        )
        # tool_error + incomplete 优先 tool_error；再测纯预算路径
        task2 = self._make_task(completed=0, stages=3, used=76, max_actions=76)
        result2 = close_task_after_stream(task2)
        task2, _, prog, events, view = self._reload(task2.pk)

        reason_blob = " ".join(
            (result2.get("acceptance") or {}).get("reasons")
            or result2.get("reasons")
            or []
        )
        passed = (
            result.get("status") == "blocked"
            and result2.get("blocker_code") == "budget_exhausted_no_business_progress"
            and task2.status == "blocked"
            and view.get("label") == "未完全完成"
            and view["business_progress"]["text"] == "0/3"
            and view["tool_budget"]["text"] == "76/76"
            and not view.get("success_visible")
            and prog["effective_config"]["completed_business_steps"] == 0
            and prog["effective_config"]["used_tool_actions"] == 76
            and any(e["event_type"] == "task_blocked" for e in events)
            and "工具调用很多但业务未推进" in reason_blob
        )
        self._record(
            "V2e_zero_progress_budget_burn",
            passed,
            f"code={result2.get('blocker_code')} label={view.get('label')} "
            f"biz={view['business_progress']['text']} "
            f"tools={view['tool_budget']['text']} "
            f"prog_cfg={prog.get('effective_config')} "
            f"view={public_progress_view(task2)}",
        )

    # --- 验证方法 3：刷新后与 DB / 事件 / 进度一致 ---
    def test_v3_refresh_matches_db_and_events(self):
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._make_task(completed=0, used=76, max_actions=76)
        close_task_after_stream(task)
        t1, snap1, prog1, ev1, view1 = self._reload(task.pk)
        t2, snap2, prog2, ev2, view2 = self._reload(task.pk)

        passed = (
            t1.status == t2.status == "blocked"
            and view1 == view2
            and view2["business_progress"]["text"] == "0/3"
            and view2["tool_budget"]["text"] == "76/76"
            and view2["label"] == "未完全完成"
            and len(ev1) == len(ev2) > 0
            and (snap1.get("status") or t1.status) == (snap2.get("status") or t2.status)
            and prog2["payload"].get("progress_view", {}).get("label") == "未完全完成"
            and not view2.get("success_visible")
        )
        self._record(
            "V3_refresh_persistence",
            passed,
            f"db={t2.status} label={view2.get('label')} "
            f"biz={view2['business_progress']['text']} "
            f"tools={view2['tool_budget']['text']} events={len(ev2)} "
            f"prog_label={prog2['payload'].get('label')}",
        )

    # --- 通过标准：未完成不显示成功；陈旧回收；全检查点才 completed ---
    def test_v4_pass_criteria_matrix(self):
        from orchestrator_integration.task_state_closure import (
            close_task_after_stream,
            public_progress_view,
            reconcile_stale_running_tasks,
        )

        stale = self._make_task(completed=0)
        from orchestrator_integration.models import AgentTask

        AgentTask.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - timedelta(seconds=2000),
            lease_expires_at=timezone.now() - timedelta(seconds=100),
        )
        out = reconcile_stale_running_tasks(older_than_seconds=900)
        stale.refresh_from_db()
        stale_view = public_progress_view(stale)
        stale_ok = (
            stale.status == "blocked"
            and out["count"] >= 1
            and not stale_view.get("success_visible")
        )

        done = self._make_task(completed=3, stages=3)
        r = close_task_after_stream(
            done, acceptance={"ok": True, "reasons": [], "evidence": True}
        )
        done.refresh_from_db()
        done_view = public_progress_view(done)
        complete_ok = (
            done.status == "completed"
            and r["status"] == "completed"
            and done_view.get("success_visible") is True
            and done_view.get("label") == "已完成"
        )

        incomplete = self._make_task(completed=1, stages=3)
        close_task_after_stream(
            incomplete,
            acceptance={"ok": True, "reasons": ["假完成"]},
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        incomplete.refresh_from_db()
        inc_view = public_progress_view(incomplete)
        no_fake = incomplete.status != "completed" and not inc_view.get(
            "success_visible"
        )

        passed = stale_ok and complete_ok and no_fake
        self._record(
            "V4_pass_criteria",
            passed,
            f"stale_ok={stale_ok} complete_ok={complete_ok} no_fake={no_fake} "
            f"reconcile={out}",
        )
