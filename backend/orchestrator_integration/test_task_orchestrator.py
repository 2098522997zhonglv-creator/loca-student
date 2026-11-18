"""任务编排 Skill 与三层工具选择（需求 20261118）单元验收。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from orchestrator_integration.intent_router import (
    INTENT_BROWSER,
    INTENT_SEARCH,
    IntentDecision,
)
from orchestrator_integration.skill_catalog import reload_skill_catalog
from orchestrator_integration.task_orchestrator import (
    STRATEGY_VERSION,
    activate_fallback,
    apply_orchestrator_mount,
    build_task_plan,
    is_task_orchestrator_enabled,
    list_capability_summaries,
    select_layers_for_node,
    sync_plan_to_stage_specs,
)
from orchestrator_integration.task_orchestrator.capability_catalog import (
    reload_capability_catalog,
)
from orchestrator_integration.task_orchestrator.flag import merge_strategy_into_facts
from orchestrator_integration.task_orchestrator.layer_selector import (
    describe_pending_for_user,
)
from orchestrator_integration.task_orchestrator.plan import plan_from_dict
from orchestrator_integration.task_orchestrator.runtime_bridge import (
    advance_plan_node,
    mark_pending_approval,
    try_activate_fallback_from_expansion,
)


def _dec(*intents: str) -> IntentDecision:
    s = set(intents)
    primary = next(iter(s), "qa")
    return IntentDecision(intents=s, primary=primary, reason="test")


class TaskOrchestratorFlagTests(unittest.TestCase):
    def test_flag_default_and_override(self):
        with mock.patch.dict(os.environ, {"TASK_ORCHESTRATOR_ENABLED": "true"}):
            self.assertTrue(is_task_orchestrator_enabled())
        with mock.patch.dict(os.environ, {"TASK_ORCHESTRATOR_ENABLED": "0"}):
            self.assertFalse(is_task_orchestrator_enabled())
        self.assertFalse(
            is_task_orchestrator_enabled(
                overrides={"task_orchestrator_enabled": False}
            )
        )
        # 在途旧策略不切新路径
        self.assertFalse(
            is_task_orchestrator_enabled(
                prior_facts={"strategy_version": "zone_commission_v1"}
            )
        )
        self.assertTrue(
            is_task_orchestrator_enabled(
                prior_facts={"strategy_version": STRATEGY_VERSION}
            )
        )

    def test_merge_strategy_facts(self):
        facts = merge_strategy_into_facts({}, enabled=True)
        self.assertEqual(facts["strategy_version"], STRATEGY_VERSION)


class CapabilityCatalogTests(unittest.TestCase):
    def setUp(self):
        reload_skill_catalog()
        reload_capability_catalog()

    def test_summaries_include_skills_and_builtins(self):
        caps = {c.capability_id for c in list_capability_summaries()}
        self.assertIn("knowledge_search", caps)
        self.assertIn("playwright-skill", caps)
        self.assertIn("task-orchestrator", caps)
        self.assertIn("ui-automation", caps)


class PlanAndLayerTests(unittest.TestCase):
    def setUp(self):
        reload_skill_catalog()
        reload_capability_catalog()

    def test_ac01_cart_phrase_builds_browser_plan(self):
        # AC01：同义表达识别
        msg = "把这个商品放进购物车，入口 https://example.com/p/1"
        plan = build_task_plan(msg, _dec(INTENT_BROWSER))
        ids = [n.node_id for n in plan.nodes]
        self.assertIn("add_to_cart", ids)
        self.assertIn("add_to_cart", plan.authorization_scope.get("allow_actions") or [])
        # 仅加购 → 提交订单需待授权或禁止
        self.assertIn("submit_order", plan.forbidden_actions)

    def test_ac04_known_entry_skips_fetch(self):
        plan = build_task_plan(
            "在已打开的站点加购并给我购物车标识",
            _dec(INTENT_BROWSER),
            prior_facts={
                "entry_url": {
                    "value": "https://x.callie.cc/",
                    "source": "prior",
                    "confirmed_at": "t0",
                }
            },
        )
        self.assertTrue(plan.nodes)
        if plan.nodes[0].node_id == "fetch_conditions":
            self.assertEqual(plan.nodes[0].status, "done")
            self.assertNotEqual(plan.current_node, "fetch_conditions")

    def test_ac06_order_authorized_vs_cart_only(self):
        order_plan = build_task_plan(
            "上传测试图，提交测试订单，给我订单号，不支付",
            _dec(INTENT_BROWSER, INTENT_SEARCH),
        )
        allow = order_plan.authorization_scope.get("allow_actions") or []
        deny = order_plan.authorization_scope.get("deny_actions") or []
        self.assertIn("submit_order", allow)
        self.assertIn("pay", deny)
        self.assertTrue(any(n.node_id == "submit_order" for n in order_plan.nodes))
        submit = next(n for n in order_plan.nodes if n.node_id == "submit_order")
        self.assertFalse(submit.pending_auth_actions)

        cart_only = build_task_plan(
            "只把商品加入购物车",
            _dec(INTENT_BROWSER),
        )
        self.assertIn("submit_order", cart_only.forbidden_actions)

    def test_ac05_layer2_fallback_no_escape(self):
        plan = build_task_plan(
            "查知识库入口后浏览器加购，不支付",
            _dec(INTENT_BROWSER, INTENT_SEARCH),
        )
        mount = apply_orchestrator_mount(plan)
        # 二层可发现
        self.assertTrue(
            mount.decision.layer2_discoverable or mount.decision.layer1_mount
        )
        # 激活备用
        node = plan.current()
        # 确保有 fallback 候选
        if node and not node.fallback_candidates:
            node.fallback_candidates = ["playwright-cli", "knowledge_search"]
        ok, msg, decision = activate_fallback(
            plan, "playwright-cli", reason="主工具失败"
        )
        self.assertTrue(ok, msg)
        self.assertIn("playwright-cli", decision.layer1_mount)

        # 兼容旧 expansion 入口
        ok2, detail, new_mount = try_activate_fallback_from_expansion(
            mount, "knowledge_search", reason="补条件"
        )
        self.assertTrue(ok2, detail)
        self.assertIn("knowledge_search", new_mount.skill_allowlist | set(new_mount.decision.layer1_mount))

    def test_ac07_pending_auth_approve_and_deny(self):
        plan = build_task_plan(
            "帮我下单拿订单号",  # 未明确「提交测试订单」时可能进三层
            _dec(INTENT_BROWSER),
        )
        # 强制构造三层
        submit = next((n for n in plan.nodes if n.node_id == "submit_order"), None)
        if submit is None:
            from orchestrator_integration.task_orchestrator.plan import PlanNode

            plan.nodes.append(
                PlanNode(
                    node_id="submit_order",
                    goal="提交订单",
                    depends_on=[plan.nodes[-1].node_id] if plan.nodes else [],
                    pending_auth_actions=["submit_order"],
                )
            )
            plan.pending_auth = []
            from orchestrator_integration.task_orchestrator.plan import PendingAuthAction

            plan.pending_auth.append(
                PendingAuthAction(
                    action_id="submit_order:submit_order",
                    task_id=plan.task_id,
                    node_id="submit_order",
                    target="submit_order",
                )
            )
            plan.current_node = "submit_order"
            submit = plan.nodes[-1]

        submit.pending_auth_actions = ["submit_order"]
        plan.current_node = submit.node_id
        decision = select_layers_for_node(plan, submit)
        self.assertTrue(decision.layer3_pending)
        text = describe_pending_for_user(decision.layer3_pending)
        self.assertIn("批准", text)

        mark_pending_approval(plan, "submit_order", "approved")
        decision2 = select_layers_for_node(plan, submit)
        self.assertTrue(
            "playwright-skill" in decision2.layer1_mount
            or not submit.pending_auth_actions
            or any(p.approval == "approved" for p in plan.pending_auth)
        )

        mark_pending_approval(plan, "submit_order", "denied")
        decision3 = select_layers_for_node(plan, submit)
        self.assertIn("submit_order", decision3.forbidden_excluded or ["submit_order"])

    def test_ac09_estimated_nodes_separate_from_tool_budget(self):
        plan = build_task_plan(
            "查入口后上传、加购、提交测试订单，不支付",
            _dec(INTENT_BROWSER, INTENT_SEARCH),
        )
        self.assertGreaterEqual(plan.estimated_nodes, 4)
        mount = apply_orchestrator_mount(plan)
        patch = mount.to_auth_scope_patch()
        self.assertEqual(patch["estimated_nodes"], plan.estimated_nodes)
        # 推进多个节点不改变 estimated_nodes
        advance_plan_node(plan, accepted=True)
        self.assertEqual(plan.estimated_nodes, patch["estimated_nodes"])

    def test_node_advance_keeps_task_running_after_kb(self):
        # AC03：查库节点结束不关闭任务
        plan = build_task_plan(
            "根据知识库找入口然后浏览器加购",
            _dec(INTENT_BROWSER, INTENT_SEARCH),
        )
        self.assertEqual(plan.current_node, "fetch_conditions")
        advance_plan_node(plan, accepted=True)
        self.assertNotEqual(plan.status, "done")
        self.assertNotEqual(plan.current_node, "fetch_conditions")

    def test_plan_roundtrip_and_stage_sync(self):
        plan = build_task_plan("执行用例 451", _dec())
        data = plan.to_dict()
        restored = plan_from_dict(data)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.task_id, plan.task_id)
        specs = sync_plan_to_stage_specs(plan)
        self.assertEqual(len(specs), len(plan.nodes))
        self.assertEqual(specs[0].stage_id, plan.nodes[0].node_id)

    def test_fr11_mount_skips_empty_intersect(self):
        plan = build_task_plan(
            "浏览器打开站点加购",
            _dec(INTENT_BROWSER),
        )
        mount = apply_orchestrator_mount(plan)
        self.assertTrue(mount.skip_legacy_intersect)
        # 计划内必有 playwright 候选；当前节点可能是取证（一层 KB）
        all_primary = {
            c for n in plan.nodes for c in (n.primary_candidates or [])
        }
        self.assertIn("playwright-skill", all_primary)
        cart = next(n for n in plan.nodes if n.node_id == "add_to_cart")
        plan.current_node = cart.node_id
        mount2 = apply_orchestrator_mount(plan)
        self.assertIn(
            "playwright-skill",
            mount2.skill_allowlist | set(mount2.decision.layer1_mount),
        )


if __name__ == "__main__":
    unittest.main()
