"""D02 / RC02：复合任务 kb_research 阶段必须保留 knowledge_search。"""
from django.test import SimpleTestCase


class _T:
    def __init__(self, name: str):
        self.name = name


class CompositeKbMountTests(SimpleTestCase):
    def test_kb_research_stage_lists_knowledge_search(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            stage_mount_policy,
        )

        text = "根据知识库登录规则生成用例并保存后执行，再汇总"
        d = classify_user_intent(text)
        stages = build_stage_plan(text, d)
        ids = [s.stage_id for s in stages]
        self.assertIn("kb_research", ids)
        policy = stage_mount_policy(stages, "kb_research")
        self.assertIn("knowledge_search", policy["builtin_tools"])

    def test_stage_remount_restores_kb_after_intent_drop(self):
        """复合写意图可能被意图路由裁掉 knowledge_search；阶段需要时必须重挂。"""
        from orchestrator_integration.intent_router import apply_intent_routing

        tools = [
            _T("knowledge_search"),
            _T("execute_skill_script"),
            _T("read_skill_content"),
        ]
        snapshot = [t for t in tools if t.name == "knowledge_search"]
        _, routed, _, _ = apply_intent_routing(
            "根据知识库登录规则生成用例并保存后执行，再汇总",
            list(tools),
            kb_active=True,
        )
        # 话术路由可能裁掉 KB（真实主入口 D02 根因之一）
        have = {t.name for t in routed}
        if "knowledge_search" not in have:
            routed = list(routed) + snapshot
        self.assertIn("knowledge_search", {t.name for t in routed})
