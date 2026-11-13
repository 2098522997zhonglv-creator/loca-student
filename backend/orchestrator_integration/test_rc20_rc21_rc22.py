"""RC20 / RC21 / RC22：多任务消歧、阶段就绪、假成功硬门禁。"""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.agent_runtime import (
    apply_goal_correction,
    create_or_resume_task,
)
from orchestrator_integration.execution_policy import evaluate_delivery_evidence
from orchestrator_integration.stage_plan import StageSpec, advance_stage, ready_stages
from orchestrator_integration.task_commission import TaskCommission
from orchestrator_integration.turn_context import (
    TURN_AMBIGUOUS,
    TURN_CORRECT,
    classify_turn_relation,
    extract_goal_correction,
    list_open_task_contexts,
)


def _commission(tid: str) -> TaskCommission:
    return TaskCommission(
        task_id=tid,
        zone_id="browser",
        playbook_id="browser_act",
        primary_skills=frozenset({"playwright-skill"}),
        auxiliary_skills=frozenset(),
        short_circuit=True,
        draft_writes=False,
        channel="light",
        max_tool_steps=8,
    )


class Rc20UnitTests(SimpleTestCase):
    def test_ambiguous_when_two_open_and_continue(self):
        from orchestrator_integration.turn_context import PriorTaskContext

        prior = PriorTaskContext(
            task_pk=1,
            public_task_id="a",
            goal="加购A",
            current_stage="browser_interact",
            auth_scope={"allow_actions": ["add_to_cart"]},
        )
        rel = classify_turn_relation(
            "继续", prior, open_count=2, resume_task_id=""
        )
        self.assertEqual(rel, TURN_AMBIGUOUS)
        rel2 = classify_turn_relation(
            "继续", prior, open_count=2, resume_task_id="a"
        )
        self.assertEqual(rel2, "continue")

    def test_correct_detects_product_swap(self):
        from orchestrator_integration.turn_context import PriorTaskContext

        prior = PriorTaskContext(
            task_pk=1,
            public_task_id="a",
            goal="加购 https://shop.example/p/1",
            current_stage="browser_interact",
            auth_scope={"allow_actions": ["add_to_cart"]},
        )
        rel = classify_turn_relation(
            "换商品 https://shop.example/p/2", prior, open_count=1
        )
        self.assertEqual(rel, TURN_CORRECT)
        patch = extract_goal_correction("换商品 https://shop.example/p/2")
        self.assertIn("target_url", patch)


class Rc21ReadyStageTests(SimpleTestCase):
    def test_independent_stage_ready_while_dependent_waits(self):
        stages = [
            StageSpec(stage_id="upload", goal="上传", depends_on=[]),
            StageSpec(stage_id="cart", goal="加购", depends_on=["upload"]),
            StageSpec(stage_id="order", goal="下单", depends_on=["cart"]),
        ]
        self.assertEqual(ready_stages(stages, completed_ids=set()), ["upload"])
        self.assertEqual(
            ready_stages(stages, completed_ids={"upload"}), ["cart"]
        )
        nxt = advance_stage(
            stages, "upload", accepted=True, completed_ids={"upload"}
        )
        self.assertEqual(nxt, "cart")


class Rc22DeliveryTests(SimpleTestCase):
    def test_input_only_claimed_complete_hard_fail(self):
        r = evaluate_delivery_evidence(
            tool_results=["已整理的输入：目标 URL=https://x 已完成加购"],
            auth_scope={"allow_actions": ["add_to_cart"]},
            target_url="https://x",
        )
        self.assertFalse(r["ok"])
        codes = [b.get("blocker_code") for b in r.get("blockers") or []]
        self.assertIn("input_only_claimed_complete", codes)

    def test_stale_cart_id_rejected(self):
        r = evaluate_delivery_evidence(
            tool_results=["cart_id=oldcart123 加购成功"],
            auth_scope={"allow_actions": ["add_to_cart"]},
            target_url="https://x",
            facts={"stale_typed_ids": ["oldcart123"], "evidence_epoch": 2},
        )
        self.assertFalse(r["ok"])
        codes = [b.get("blocker_code") for b in r.get("blockers") or []]
        self.assertIn("stale_typed_id", codes)

    def test_success_products_and_remaining(self):
        r = evaluate_delivery_evidence(
            tool_results=[
                'status=succeeded cart_id=abc99999 source_url=https://x'
            ],
            auth_scope={"allow_actions": ["add_to_cart"]},
            target_url="https://x",
            stage_plan=[
                {"stage_id": "upload"},
                {"stage_id": "cart"},
                {"stage_id": "summarize"},
            ],
            facts={"completed_stage_ids": ["upload"], "evidence_epoch": 1},
        )
        self.assertTrue(any(p.get("id") == "abc99999" for p in r["success_products"]))
        self.assertIn("cart", r["remaining_checkpoints"])
        self.assertNotIn("summarize", r["remaining_checkpoints"])


class Rc20DbTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rc20u", password="x")
        self.chat = ChatSession.objects.create(
            user=self.user, session_id="sess-rc20", title="t"
        )

    def test_list_open_and_correction_invalidates(self):
        t1 = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="加购商品A https://shop.example/a",
            commission=_commission("t1"),
            auth_scope={"allow_actions": ["add_to_cart"]},
            stage_plan=[{"stage_id": "browser_interact"}],
            lease_owner="w1",
        )
        # 强制完成第一条租约，创建第二条开放任务
        from orchestrator_integration.models import AgentTask

        AgentTask.objects.filter(pk=t1.pk).update(lease_owner="", status="blocked")
        t2 = create_or_resume_task(
            session=self.chat,
            user=self.user,
            project_id=None,
            goal="加购商品B https://shop.example/b",
            commission=_commission("t2"),
            auth_scope={"allow_actions": ["add_to_cart"]},
            stage_plan=[{"stage_id": "browser_interact"}],
            lease_owner="w2",
        )
        opens = list_open_task_contexts(
            session_id="sess-rc20", user_id=self.user.id
        )
        self.assertGreaterEqual(len(opens), 2)

        t2.facts = {"cart_id": "old123", "evidence_epoch": 0}
        t2.save(update_fields=["facts"])
        corr = apply_goal_correction(
            t2, "换商品 https://shop.example/c"
        )
        self.assertTrue(corr["ok"])
        t2.refresh_from_db()
        self.assertEqual(t2.task_scope.get("target_url"), "https://shop.example/c")
        self.assertEqual(t2.facts.get("evidence_epoch"), 1)
        self.assertIn("old123", t2.facts.get("stale_typed_ids") or [])
        self.assertNotIn("cart_id", t2.facts)
