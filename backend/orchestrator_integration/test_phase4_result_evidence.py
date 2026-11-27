"""阶段 4 验收：结果证据与交付 — 绑定证据、矛盾禁止完成、交付一致。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


class Phase4ResultEvidenceAcceptance(TestCase):
    RESULTS = []

    @classmethod
    def tearDownClass(cls):
        print("\n===== 阶段4验收结果 =====")
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
        user = User.objects.create_user(username=f"p4_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"p4-sess-{n}", title="p4"
        )
        defaults = dict(
            session=chat,
            user=user,
            public_task_id=f"p4{n:08d}",
            goal="加购一件商品并验证购物车",
            status="running",
            current_stage="assert_and_deliver",
            stage_plan=[
                {
                    "stage_id": "query_cases",
                    "completion_checks": [
                        {
                            "criterion_id": "q1",
                            "description": "查询目标商品",
                            "required_claim": "observed",
                            "evidence_kind": "tool_return",
                        }
                    ],
                },
                {
                    "stage_id": "run_or_browser",
                    "completion_checks": [
                        {
                            "criterion_id": "w1",
                            "description": "购物车数量增加",
                            "required_claim": "observed",
                            "evidence_kind": "page_state",
                        }
                    ],
                },
                {
                    "stage_id": "assert_and_deliver",
                    "completion_checks": [
                        {
                            "criterion_id": "a1",
                            "description": "交付前断言购物车",
                            "required_claim": "verified",
                            "evidence_kind": "page_state",
                        }
                    ],
                },
            ],
            budget={
                "expected_business_steps": 3,
                "completed_business_steps": 3,
                "used_tool_actions": 5,
                "max_tool_actions": 80,
            },
            facts={
                "completed_stage_ids": [
                    "query_cases",
                    "run_or_browser",
                    "assert_and_deliver",
                ]
            },
        )
        defaults.update(kwargs)
        return AgentTask.objects.create(**defaults)

    def _step(
        self,
        task,
        *,
        stage_id: str,
        tool_name: str,
        status: str = "succeeded",
        summary: str = "",
        before=None,
        after=None,
        claim_level: str = "",
        expected=None,
        observed=None,
        op_id: str = "",
        no_side_effect: bool = False,
    ):
        from orchestrator_integration.agent_runtime import record_step_result

        evidence = {}
        if before is not None:
            evidence["side_effect_before"] = before
        if after is not None:
            evidence["side_effect_after"] = after
        if claim_level:
            evidence["claim_level"] = claim_level
        structured = {
            "status": status,
            "operation_id": op_id or f"op-{tool_name}-{stage_id}",
            "summary": summary,
            "evidence": evidence,
        }
        if expected is not None:
            structured["expected"] = expected
        if observed is not None:
            structured["observed"] = observed
        if no_side_effect:
            structured["no_side_effect"] = True
        if claim_level:
            structured["claim_level"] = claim_level
        return record_step_result(
            task,
            tool_name=tool_name,
            tool_input={"skill_name": tool_name, "command": summary},
            structured=structured,
            summary=summary,
            stage_id=stage_id,
        )

    def test_v1_success_but_unchanged_blocks_complete(self):
        """验证方法1：工具成功但页面状态未变 → 矛盾 → 禁止 completed。"""
        from orchestrator_integration.agent_runtime import apply_acceptance
        from orchestrator_integration.result_evidence_delivery import (
            detect_evidence_contradictions,
            gate_completion_on_evidence,
        )

        task = self._task()
        self._step(
            task,
            stage_id="run_or_browser",
            tool_name="loca-stude",
            summary="add to cart",
            before={"cart": 0},
            after={"cart": 0},  # 未改变
            claim_level="executed",
            op_id="op-unchanged",
        )
        task.refresh_from_db()
        from orchestrator_integration.result_evidence_delivery import _step_rows

        steps = _step_rows(task)
        issues = detect_evidence_contradictions(steps)
        gated = gate_completion_on_evidence(task, {"ok": True, "reasons": []})
        evt = apply_acceptance(task, {"ok": True, "reasons": []})
        task.refresh_from_db()
        self._record(
            "P4-V1-contradiction-blocks",
            any(i.get("code") == "success_but_state_unchanged" for i in issues)
            and gated.get("ok") is False
            and task.status == "blocked"
            and evt.get("status") == "blocked",
            f"issues={[i.get('code') for i in issues]} status={task.status} "
            f"gate_ok={gated.get('ok')}",
        )

    def test_v2_partial_success_listed_in_delivery(self):
        """验证方法2：部分成功部分失败 → 交付列出失败与未完成项。"""
        from orchestrator_integration.result_evidence_delivery import (
            build_delivery_report,
            gate_completion_on_evidence,
        )

        task = self._task()
        self._step(
            task,
            stage_id="query_cases",
            tool_name="knowledge_search",
            summary="found sku",
            before={"hits": 0},
            after={"hits": 1},
            claim_level="observed",
            op_id="op-q-ok",
        )
        self._step(
            task,
            stage_id="run_or_browser",
            tool_name="loca-stude",
            status="failed",
            summary="add cart failed",
            claim_level="executed",
            op_id="op-w-fail",
            no_side_effect=True,
        )
        # assert 阶段无证据
        report = build_delivery_report(task)
        gated = gate_completion_on_evidence(task, {"ok": True})
        self._record(
            "P4-V2-partial-delivery",
            len(report.get("failures") or []) >= 1
            and len(report.get("incomplete_items") or []) >= 1
            and report.get("ok") is False
            and gated.get("ok") is False
            and "goal" in report
            and "step_results" in report,
            f"failures={len(report.get('failures') or [])} "
            f"incomplete={len(report.get('incomplete_items') or [])} "
            f"summary={report.get('summary')}",
        )

    def test_v3_consistent_complete_with_verified_evidence(self):
        """验证方法3：最终状态、报告、证据一致方可 completed。"""
        from orchestrator_integration.agent_runtime import apply_acceptance
        from orchestrator_integration.result_evidence_delivery import (
            CLAIM_OBSERVED,
            CLAIM_VERIFIED,
            bind_criteria_to_evidence,
            build_delivery_report,
            classify_claim_level,
        )
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task()
        self._step(
            task,
            stage_id="query_cases",
            tool_name="knowledge_search",
            summary="sku ok",
            before={"hits": 0},
            after={"hits": 1},
            claim_level=CLAIM_OBSERVED,
            op_id="op-q",
        )
        self._step(
            task,
            stage_id="run_or_browser",
            tool_name="loca-stude",
            summary="cart +1",
            before={"cart": 0},
            after={"cart": 1},
            claim_level=CLAIM_OBSERVED,
            op_id="op-w",
        )
        self._step(
            task,
            stage_id="assert_and_deliver",
            tool_name="assert_task_postcondition",
            summary="cart verified",
            before={"cart": 1},
            after={"cart": 1},
            claim_level=CLAIM_VERIFIED,
            expected={"cart": 1},
            observed={"cart": 1},
            op_id="op-a",
            no_side_effect=True,  # 断言观察相等不算矛盾
        )
        bindings = bind_criteria_to_evidence(task)
        report = build_delivery_report(task)
        level = classify_claim_level(
            tool_name="assert_task_postcondition",
            step_status="succeeded",
            structured={"status": "succeeded"},
        )
        evt = apply_acceptance(task, {"ok": True, "reasons": []})
        task.refresh_from_db()
        # 流结束收口保持 completed
        closure = close_task_after_stream(task, acceptance={"ok": True})
        task.refresh_from_db()
        all_ok = all(b.get("claim_ok") for b in bindings)
        self._record(
            "P4-V3-consistent-complete",
            all_ok
            and report.get("ok") is True
            and level == CLAIM_VERIFIED
            and task.status == "completed"
            and evt.get("status") == "completed"
            and closure.get("status") == "completed"
            and len(report.get("completed_items") or []) == 3
            and not report.get("contradictions")
            and (task.acceptance or {}).get("delivery", {}).get("ok") is True,
            f"bindings_ok={all_ok} status={task.status} "
            f"completed_items={len(report.get('completed_items') or [])} "
            f"delivery_ok={(task.acceptance or {}).get('delivery', {}).get('ok')}",
        )

    def test_v4_missing_evidence_cannot_complete(self):
        """通过标准：没有证据不能完成验收。"""
        from orchestrator_integration.result_evidence_delivery import (
            gate_completion_on_evidence,
        )
        from orchestrator_integration.task_state_closure import close_task_after_stream

        task = self._task()  # 有准则但无步骤证据
        gated = gate_completion_on_evidence(task, {"ok": True})
        closure = close_task_after_stream(task, acceptance={"ok": True})
        task.refresh_from_db()
        self._record(
            "P4-V4-no-evidence-no-complete",
            gated.get("ok") is False
            and task.status == "blocked"
            and closure.get("action") == "blocked"
            and any(
                "缺少合格证据" in r or "missing" in str(r).lower()
                for r in (gated.get("acceptance") or {}).get("reasons") or []
            ),
            f"status={task.status} action={closure.get('action')} "
            f"reasons={(gated.get('acceptance') or {}).get('reasons')}",
        )

    def test_v5_claim_levels_executed_observed_verified(self):
        """区分已执行 / 已观察 / 已验证。"""
        from orchestrator_integration.result_evidence_delivery import (
            CLAIM_EXECUTED,
            CLAIM_OBSERVED,
            CLAIM_VERIFIED,
            classify_claim_level,
        )

        e = classify_claim_level(
            tool_name="loca-stude",
            step_status="succeeded",
            structured={"status": "succeeded"},
            evidence={},
        )
        o = classify_claim_level(
            tool_name="loca-stude",
            step_status="succeeded",
            structured={"status": "succeeded", "data": {"page_url": "https://x"}},
            evidence={"side_effect_before": {"n": 0}, "side_effect_after": {"n": 1}},
        )
        v = classify_claim_level(
            tool_name="assert_task_postcondition",
            step_status="succeeded",
            structured={"status": "succeeded"},
        )
        self._record(
            "P4-V5-claim-levels",
            e == CLAIM_EXECUTED and o == CLAIM_OBSERVED and v == CLAIM_VERIFIED,
            f"executed={e} observed={o} verified={v}",
        )
