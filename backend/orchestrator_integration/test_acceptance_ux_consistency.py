"""验收 UX 一致性：single 检查点口径、写入失败判定、证据门禁对齐业务进度。"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from langgraph_integration.models import ChatSession


class AcceptanceUxConsistencyTests(TestCase):
    def _task(self, **kwargs):
        from orchestrator_integration.models import AgentTask

        User = get_user_model()
        n = AgentTask.objects.count()
        user = User.objects.create_user(username=f"ux_{n}", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id=f"ux-sess-{n}", title="ux"
        )
        defaults = dict(
            session=chat,
            user=user,
            public_task_id=f"ux{n:08d}",
            goal="把商品中心测试文档写入知识库",
            status="running",
            current_stage="single",
            stage_plan=[
                {
                    "stage_id": "single",
                    "goal": "完成本轮用户目标",
                    "acceptance": "完成用户目标且无越权",
                    "completion_checks": ["完成用户目标且无越权"],
                }
            ],
            budget={
                "expected_business_steps": 0,
                "completed_business_steps": 0,
                "used_tool_actions": 5,
                "max_tool_actions": 80,
            },
            facts={
                "task_plan": {
                    "nodes": [
                        {
                            "node_id": "single",
                            "completion_checks": ["完成用户目标且无越权"],
                        }
                    ]
                }
            },
        )
        defaults.update(kwargs)
        return AgentTask.objects.create(**defaults)

    def test_single_plan_does_not_invent_missing_evidence_checkpoint(self):
        from orchestrator_integration.result_evidence_delivery import (
            gate_completion_on_evidence,
            list_acceptance_criteria,
        )
        from orchestrator_integration.task_execution_config import (
            expected_business_steps_from_plan,
        )
        from orchestrator_integration.task_state_closure import public_progress_view

        task = self._task()
        expected = expected_business_steps_from_plan(task.stage_plan)
        criteria = list_acceptance_criteria(task)
        gated = gate_completion_on_evidence(task, {"ok": True, "reasons": []})
        view = public_progress_view(task)
        reasons = "；".join((gated.get("acceptance") or {}).get("reasons") or [])

        self.assertEqual(expected, 0)
        self.assertEqual(criteria, [])
        self.assertEqual(view["business_progress"]["text"], "0/-")
        self.assertNotIn("single:check:0", reasons)
        self.assertNotIn("missing_evidence", reasons)
        # 无业务检查点时，证据门禁不得仅因 single 占位条件翻失败
        self.assertTrue(gated.get("ok"))

    def test_business_persist_stage_still_requires_evidence(self):
        from orchestrator_integration.result_evidence_delivery import (
            list_acceptance_criteria,
        )

        task = self._task(
            current_stage="persist",
            stage_plan=[
                {
                    "stage_id": "persist",
                    "acceptance": "实体 ID 回读成功",
                    "completion_checks": ["实体 ID 回读成功"],
                }
            ],
            budget={
                "expected_business_steps": 1,
                "completed_business_steps": 0,
                "used_tool_actions": 2,
                "max_tool_actions": 80,
            },
            facts={},
        )
        criteria = list_acceptance_criteria(task)
        self.assertEqual(len(criteria), 1)
        self.assertEqual(criteria[0]["stage_id"], "persist")

    def test_platform_write_bare_error_word_is_not_write_path_failure(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        # 检索旁白含 error，但未见写入动作 → 不得冒充「写入路径异常」
        acc = evaluate_post_condition(
            post_condition="write",
            tool_results=[
                "knowledge_search: found docs; note: previous error was ignored",
                "read_skill_content: loca-stude SKILL.md loaded",
            ],
            playbook_id="platform_write",
        )
        self.assertTrue(
            acc["ok"],
            msg=f"unexpected block reasons={acc.get('reasons')}",
        )
        self.assertFalse(
            any("写入路径异常" in str(r) for r in (acc.get("reasons") or []))
        )

    def test_platform_write_structured_fail_without_write_attempt_is_soft(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="write",
            tool_results=['{"status":"failed","message":"skill inactive"}'],
            playbook_id="platform_write",
        )
        self.assertFalse(acc["ok"])
        reasons = acc.get("reasons") or []
        self.assertTrue(any("未产生可确认的写入草稿" in str(r) for r in reasons))
        self.assertFalse(any("写入路径异常且无确认草稿" in str(r) for r in reasons))

    def test_platform_write_real_write_failure_keeps_hard_reason(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="write",
            tool_results=[
                'execute_skill_script --action write_doc {"status":"failed","ok":false}'
            ],
            playbook_id="platform_write",
        )
        self.assertFalse(acc["ok"])
        self.assertTrue(
            any("写入路径异常且无确认草稿" in str(r) for r in (acc.get("reasons") or []))
        )

    def test_platform_write_draft_confirm_still_passes(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="write",
            tool_results=[
                '{"status":"needs_confirmation","draft":{"title":"商品中心测试文档"}}'
            ],
            playbook_id="platform_write",
        )
        self.assertTrue(acc["ok"])
