"""阶段 5 验收：生产可观测性与稳定性。"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class Phase5ObservabilityAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 阶段5验收结果 =====")
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
        user = User.objects.create_user(username=f"p5_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"p5-sess-{n}", title="p5"
        )
        defaults = dict(
            session=chat,
            user=user,
            public_task_id=f"p5{n:08d}",
            goal="可观测性验收任务",
            status="running",
            current_stage="run_or_browser",
            stage_plan=[{"stage_id": "run_or_browser"}],
            budget={"expected_business_steps": 1, "completed_business_steps": 0},
            facts={},
        )
        defaults.update(kwargs)
        return AgentTask.objects.create(**defaults)

    def test_v1_unified_lifecycle_ids(self):
        """验证：任务/步骤统一 ID、起止、状态、重试、错误类型。"""
        from orchestrator_integration.agent_runtime import record_step_result
        from orchestrator_integration.production_observability import (
            step_lifecycle,
            task_lifecycle,
            tool_call_lifecycle,
        )

        task = self._task()
        step = record_step_result(
            task,
            tool_name="loca-stude",
            tool_input={"command": "python x.py --action add"},
            structured={
                "status": "failed",
                "operation_id": "op-fail-1",
                "error_type": "tool_timeout",
                "summary": "timed out",
                "duration_ms": 1200,
                "evidence": {"idempotency_key": "k1"},
            },
            summary="timed out",
            stage_id="run_or_browser",
        )
        tl = task_lifecycle(task)
        sl = step_lifecycle(step, task_id=task.public_task_id)
        cl = tool_call_lifecycle(
            call_id="call-1",
            task_id=task.public_task_id,
            tool_name="loca-stude",
            status="failed",
            started_at=timezone.now().isoformat(),
            ended_at=timezone.now().isoformat(),
            attempt_count=2,
            error_type="tool_timeout",
            error_message="timed out",
            duration_ms=1200,
        )
        keys = {"id", "status", "started_at", "ended_at", "attempt_count", "error_type"}
        self._record(
            "P5-V1-lifecycle",
            keys.issubset(tl)
            and keys.issubset(sl)
            and keys.issubset(cl)
            and sl.get("error_type") == "tool_timeout"
            and cl.get("attempt_count") == 2
            and tl.get("entity") == "task",
            f"task={tl.get('id')} step_err={sl.get('error_type')} "
            f"call_attempts={cl.get('attempt_count')}",
        )

    def test_v2_hanging_reclaim_and_trace(self):
        """验证方法1/2：注入中断 → 发现悬挂 → 回收 → 可按 task_id 追踪。"""
        from orchestrator_integration.production_observability import (
            find_hanging_tasks,
            reclaim_stale_tasks,
            trace_task,
        )

        task = self._task(
            status="running",
            lease_expires_at=timezone.now() - timedelta(seconds=120),
        )
        # 人为陈旧
        type(task).objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(seconds=1200)
        )
        hanging = find_hanging_tasks(older_than_seconds=900)
        found = any(h.get("task_id") == task.public_task_id for h in hanging)
        result = reclaim_stale_tasks(older_than_seconds=900, emit_alerts=True)
        task.refresh_from_db()
        trace = trace_task(task.public_task_id)
        audit_ok = "stale_reclaim" in (trace.get("audit_types_present") or []) or any(
            e.get("event_type") == "stale_reclaim" for e in (trace.get("events") or [])
        )
        self._record(
            "P5-V2-hang-reclaim-trace",
            found
            and result.get("reclaimed", 0) >= 1
            and task.status == "blocked"
            and trace.get("ok") is True
            and trace.get("db_status") == "blocked"
            and audit_ok
            and trace.get("event_count", 0) >= 1,
            f"found={found} reclaimed={result.get('reclaimed')} "
            f"status={task.status} events={trace.get('event_count')} "
            f"audits={trace.get('audit_types_present')}",
        )

    def test_v3_ops_metrics_match_sample(self):
        """验证方法3：统计与抽样记录一致（完成率/耗时/重复）。"""
        from orchestrator_integration.agent_runtime import (
            apply_acceptance,
            record_step_result,
        )
        from orchestrator_integration.production_observability import (
            compute_ops_metrics,
        )

        # 完成任务
        t1 = self._task(status="running")
        record_step_result(
            t1,
            tool_name="loca-stude",
            tool_input={"command": "ok"},
            structured={
                "status": "succeeded",
                "operation_id": "op-ok",
                "summary": "done",
                "evidence": {
                    "idempotency_key": "dup-key",
                    "side_effect_before": {"n": 0},
                    "side_effect_after": {"n": 1},
                },
            },
            summary="done",
            stage_id="run_or_browser",
        )
        # 无验收条件 → 可完成
        apply_acceptance(t1, {"ok": True, "reasons": []})
        t1.refresh_from_db()

        # 同幂等键第二次（重复）
        t2 = self._task(status="running")
        for i in range(2):
            record_step_result(
                t2,
                tool_name="loca-stude",
                tool_input={"command": "replay"},
                structured={
                    "status": "succeeded",
                    "operation_id": f"op-dup-{i}",
                    "summary": "replay",
                    "evidence": {"idempotency_key": "same-dup-key"},
                    "no_side_effect": True,
                },
                summary="replay",
                stage_id="run_or_browser",
            )
        # 续跑事实
        facts = dict(t2.facts or {})
        facts["resume_plan"] = {"hint": "续跑"}
        t2.facts = facts
        t2.budget = {**(t2.budget or {}), "resume_count": 1}
        t2.save(update_fields=["facts", "budget", "updated_at"])

        metrics = compute_ops_metrics(limit=50)
        self._record(
            "P5-V3-metrics-consistency",
            metrics.get("tasks_sampled", 0) >= 2
            and metrics.get("completion_rate") is not None
            and metrics.get("duplicate_idempotency_keys", 0) >= 1
            and metrics.get("resume_attempts", 0) >= 1
            and (
                metrics.get("avg_duration_ms") is None
                or metrics.get("avg_duration_ms") >= 0
            ),
            f"sampled={metrics.get('tasks_sampled')} "
            f"completion={metrics.get('completion_rate')} "
            f"dup_keys={metrics.get('duplicate_idempotency_keys')} "
            f"resume={metrics.get('resume_attempts')} "
            f"avg_ms={metrics.get('avg_duration_ms')}",
        )

    def test_v4_audit_status_auth_delivery(self):
        """审计：状态迁移 / 授权变更 / 最终交付可查。"""
        from orchestrator_integration.agent_runtime import apply_acceptance
        from orchestrator_integration.models import TaskEvent
        from orchestrator_integration.production_observability import (
            audit_auth_changed,
            audit_status_transition,
            trace_task,
        )

        task = self._task(status="running")
        audit_status_transition(
            task,
            from_status="queued",
            to_status="running",
            reason="start",
        )
        audit_auth_changed(
            task,
            prior_scope={"allow_actions": ["platform_write"]},
            new_scope={
                "allow_actions": ["platform_write"],
                "pending_auth_actions": ["submit_order"],
                "continue_auth_policy": {"deferred_new_allows": ["submit_order"]},
            },
            source="continue_merge",
        )
        apply_acceptance(task, {"ok": False, "reasons": ["injected_timeout"]})
        task.refresh_from_db()
        types = set(
            TaskEvent.objects.filter(task=task).values_list("event_type", flat=True)
        )
        trace = trace_task(task.public_task_id)
        self._record(
            "P5-V4-audit-trail",
            "status_transition" in types
            and "auth_changed" in types
            and "final_delivery" in types
            and "task_blocked" in types
            and trace.get("ok") is True,
            f"types={sorted(types)} audits={trace.get('audit_types_present')}",
        )

    def test_v5_anomaly_alert_on_hanging(self):
        """异常告警：悬挂任务触发 alert。"""
        from orchestrator_integration.production_observability import (
            detect_anomalies,
            emit_anomaly_alerts,
        )

        task = self._task(
            status="running",
            lease_expires_at=timezone.now() - timedelta(seconds=60),
        )
        type(task).objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(seconds=2000)
        )
        detected = detect_anomalies(hanging_older_than_seconds=900)
        emitted = emit_anomaly_alerts(hanging_older_than_seconds=900)
        codes = {a.get("code") for a in (detected.get("alerts") or [])}
        self._record(
            "P5-V5-anomaly-alert",
            detected.get("ok") is False
            and "hanging_tasks" in codes
            and len(emitted.get("alerts") or []) >= 1,
            f"codes={sorted(codes)} emitted={len(emitted.get('alerts') or [])}",
        )

    def test_v6_quality_metrics_exposes_ops(self):
        """质量指标 API 载荷含阶段5 ops 字段。"""
        from orchestrator_integration.agent_runtime import quality_metrics

        self._task(status="completed", completed_at=timezone.now())
        m = quality_metrics(limit=50)
        self._record(
            "P5-V6-quality-ops",
            "ops" in m
            and "anomalies" in m
            and "hanging_task_count" in m
            and "completion_rate" in m,
            f"keys={[k for k in ('ops','anomalies','hanging_task_count','resume_success_rate','duplicate_op_rate','avg_duration_ms') if k in m]}",
        )
