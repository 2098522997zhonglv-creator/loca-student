"""阶段 2 验收：续跑幂等 — 中断恢复跳过已完成、未知先核对、重复继续不重放。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


class Phase2ResumeIdempotencyAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 阶段2验收结果 =====")
        for row in cls.RESULTS:
            print(json.dumps(row, ensure_ascii=False))
        print("===== END =====\n")
        super().tearDownClass()

    def _record(self, case_id: str, ok: bool, detail: str):
        self.RESULTS.append({"case": case_id, "ok": ok, "detail": detail})
        self.assertTrue(ok, msg=f"{case_id}: {detail}")

    def _task(self):
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        n = AgentTask.objects.count()
        user = User.objects.create_user(username=f"p2_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"p2-sess-{n}", title="p2"
        )
        return AgentTask.objects.create(
            session=chat,
            user=user,
            public_task_id=f"p2{n:08d}",
            goal="两步副作用任务",
            status="running",
            current_stage="run_or_browser",
            stage_plan=[
                {"stage_id": "query_cases"},
                {"stage_id": "run_or_browser"},
                {"stage_id": "assert_and_deliver"},
            ],
            budget={
                "expected_business_steps": 3,
                "completed_business_steps": 1,
                "used_tool_actions": 4,
                "max_tool_actions": 80,
            },
            facts={"completed_stage_ids": ["query_cases"]},
        )

    def _record_success(self, task, *, command: str, op_id: str):
        from orchestrator_integration.agent_runtime import record_step_result
        from orchestrator_integration.idempotency import build_idempotency_key

        key = build_idempotency_key(
            task_id=task.public_task_id,
            skill_name="loca-stude",
            command=command,
        )
        return record_step_result(
            task,
            tool_name="loca-stude",
            tool_input={"skill_name": "loca-stude", "command": command},
            structured={
                "status": "succeeded",
                "operation_id": op_id,
                "idempotency_key": key,
                "summary": f"wrote {op_id}",
                "evidence": {
                    "idempotency_key": key,
                    "side_effect_before": {"cart": 0},
                    "side_effect_after": {"cart": 1},
                },
            },
            summary=f"wrote {op_id}",
            stage_id="run_or_browser",
        )

    def test_v1_resume_skips_completed_after_interrupt(self):
        """验证方法1：第2步后中断，恢复计划跳过已完成。"""
        from orchestrator_integration.resume_execution import (
            build_resume_plan,
            guard_side_effect_replay,
        )
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task()
        cmd1 = "python x.py --action add_testcase --name row1"
        cmd2 = "python x.py --action add_testcase --name row2"
        self._record_success(task, command=cmd1, op_id="op-step1")
        self._record_success(task, command=cmd2, op_id="op-step2")
        # 模拟进程中断收口
        close_task_after_stream(
            task,
            auto_continue_attempted=True,
            auto_continue_succeeded=False,
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")

        plan = build_resume_plan(task)
        completed_ops = {
            s.get("operation_id") for s in (plan.get("completed_steps") or [])
        }
        guard1 = guard_side_effect_replay(
            task, skill_name="loca-stude", command=cmd1
        )
        guard2 = guard_side_effect_replay(
            task, skill_name="loca-stude", command=cmd2
        )
        remaining_ok = "assert_and_deliver" in (plan.get("remaining_stages") or [])
        passed = (
            len(plan.get("completed_steps") or []) >= 2
            and "op-step1" in completed_ops
            and "op-step2" in completed_ops
            and guard1.get("skip")
            and guard2.get("skip")
            and guard1.get("reason") == "idempotent_replay_blocked"
            and remaining_ok
        )
        self._record(
            "P2_V1_skip_completed",
            passed,
            f"completed={len(plan.get('completed_steps') or [])} "
            f"guard1={guard1.get('reason')} remaining={plan.get('remaining_stages')}",
        )

    def test_v2_timeout_unknown_reconcile_before_replay(self):
        """验证方法2：超时未知 → 禁止重放，先核对。"""
        from orchestrator_integration.agent_runtime import record_step_result
        from orchestrator_integration.idempotency import build_idempotency_key
        from orchestrator_integration.resume_execution import (
            guard_side_effect_replay,
            resume_preflight_v2,
        )

        task = self._task()
        cmd = "python x.py --action add_testcase --name alpha"
        key = build_idempotency_key(
            task_id=task.public_task_id, skill_name="loca-stude", command=cmd
        )
        record_step_result(
            task,
            tool_name="loca-stude",
            tool_input={"command": cmd},
            structured={
                "status": "unknown",
                "operation_id": "op-timeout",
                "idempotency_key": key,
                "summary": "timeout while waiting for response",
                "evidence": {
                    "idempotency_key": key,
                    "side_effect_before": {"count": 0},
                    # after 未知：远端可能已成功
                },
            },
            summary="timeout while waiting for response",
            stage_id="run_or_browser",
        )
        pre = resume_preflight_v2(task)
        guard = guard_side_effect_replay(
            task, skill_name="loca-stude", command=cmd
        )
        passed = (
            pre.get("must_reconcile")
            and guard.get("skip")
            and guard.get("must_reconcile")
            and guard.get("reason") == "uncertain_must_reconcile"
            and bool((guard.get("reconcile") or {}).get("must_reconcile"))
        )
        self._record(
            "P2_V2_uncertain_reconcile",
            passed,
            f"preflight={pre.get('must_reconcile')} guard={guard.get('reason')} "
            f"action={(guard.get('reconcile') or {}).get('reconcile_action')}",
        )

    def test_v3_repeat_continue_no_duplicate_side_effect(self):
        """验证方法3：重复继续指令 → 同键副作用不重放。"""
        from orchestrator_integration.resume_execution import guard_side_effect_replay
        from orchestrator_integration.runtime.task_store import TaskStore

        task = self._task()
        cmd = "python x.py --action add_testcase --name beta"
        self._record_success(task, command=cmd, op_id="op-beta")

        # 第一次「继续」
        store = TaskStore()
        store.apply_resume_scope(
            task,
            goal="继续",
            relation="continue",
            continue_like=True,
            auth_scope=None,
            commission=None,
        )
        task.refresh_from_db()
        g1 = guard_side_effect_replay(task, skill_name="loca-stude", command=cmd)
        # 第二次「继续」
        store.apply_resume_scope(
            task,
            goal="继续",
            relation="continue",
            continue_like=True,
            auth_scope=None,
            commission=None,
        )
        g2 = guard_side_effect_replay(task, skill_name="loca-stude", command=cmd)
        plan_hint = (task.facts or {}).get("resume_plan") or {}
        passed = (
            g1.get("skip")
            and g2.get("skip")
            and g1.get("operation_id") == g2.get("operation_id") == "op-beta"
            and int(plan_hint.get("completed_count") or 0) >= 1
            and "跳过" in (plan_hint.get("hint") or "")
        )
        self._record(
            "P2_V3_repeat_continue",
            passed,
            f"g1={g1.get('reason')} g2={g2.get('reason')} "
            f"op={g2.get('operation_id')} hint_len={len(plan_hint.get('hint') or '')}",
        )

    def test_side_effect_before_after_persisted(self):
        task = self._task()
        cmd = "python x.py --action add_testcase --name gamma"
        step = self._record_success(task, command=cmd, op_id="op-gamma")
        ev = step.evidence or {}
        passed = (
            ev.get("idempotency_key")
            and ev.get("side_effect_before") == {"cart": 0}
            and ev.get("side_effect_after") == {"cart": 1}
            and bool(ev.get("input_summary") or step.tool_input)
        )
        self._record(
            "P2_side_effect_snapshots",
            passed,
            f"keys={list(ev.keys())} before={ev.get('side_effect_before')}",
        )
