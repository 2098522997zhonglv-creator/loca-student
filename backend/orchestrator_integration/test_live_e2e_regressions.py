"""真实任务暴露的路由、协议重复包装与 LangChain 流事件回归。"""
import ast
import json
import unittest
from pathlib import Path

from orchestrator_integration.intent_router import classify_user_intent
from orchestrator_integration.task_orchestrator.plan import build_task_plan
from orchestrator_integration.tool_result import adapt_mcp_tool_result


class LivePlanTests(unittest.TestCase):
    def test_browser_cart_verification_is_not_platform_case_execution(self):
        text = (
            "执行真实复杂测试验收：先在知识库查询定制商品加购要求，"
            "在 https://callie-us-dev-ai-product-description-web.a.callie.cc "
            "用两组参数检查预览后加入购物车，核对cart_id/cart_item_id"
            "（以真实接口字段为准），数量金额和截图。不提交订单、不支付。"
        )
        plan = build_task_plan(text, classify_user_intent(text))
        ids = [n.node_id for n in plan.nodes]
        self.assertIn("fetch_conditions", ids)
        self.assertIn("add_to_cart", ids)
        self.assertIn("verify_deliver", ids)
        self.assertNotIn("query_cases", ids)
        self.assertNotIn("submit_order", ids)

    def test_explicit_api_case_keeps_browser_fallback(self):
        text = "执行接口场景并断言，失败后用浏览器复核"
        plan = build_task_plan(text, classify_user_intent(text))
        self.assertIn("query_cases", [n.node_id for n in plan.nodes])

    def test_explicit_negative_order_and_payment_scope(self):
        from orchestrator_integration.stage_plan import infer_auth_scope
        scope = infer_auth_scope("加入购物车，不提交订单、不付款")
        self.assertEqual(scope["allow_actions"], ["add_to_cart"])
        self.assertIn("submit_order", scope["deny_actions"])
        self.assertIn("pay", scope["deny_actions"])


class LiveProtocolTests(unittest.TestCase):
    def test_serialized_tool_result_is_idempotent_at_mcp_and_sse_boundaries(self):
        payload = {
            "status": "succeeded", "operation_id": "real-operation-1",
            "data": {"raw_text": "Observed cart_count=0", "cart_count": 0},
            "duration_ms": 125.5, "summary": "购物车为空",
        }
        first = adapt_mcp_tool_result(json.dumps(payload), tool_name="execute_skill_script")
        second = adapt_mcp_tool_result(first.to_tool_text(), tool_name="execute_skill_script")
        third = adapt_mcp_tool_result(
            [{"type": "text", "text": second.to_tool_text()}], tool_name="execute_skill_script"
        )
        self.assertEqual(third.operation_id, "real-operation-1")
        self.assertEqual(third.data, payload["data"])
        self.assertEqual(third.duration_ms, 125.5)
        self.assertEqual(first.to_dict(), third.to_dict())

    def test_failure_is_preserved_across_serialized_boundary(self):
        result = adapt_mcp_tool_result(json.dumps({
            "status": "failed", "operation_id": "op-fail", "data": {},
            "error_code": "cart_rejected", "summary": "加购被拒绝",
        }), tool_name="execute_skill_script")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "cart_rejected")
        self.assertEqual(result.operation_id, "op-fail")

    def test_skill_document_timeout_example_is_not_an_execution_timeout(self):
        result = adapt_mcp_tool_result(
            "# Playwright Skill\npage.goto(url, {timeout: 30000});\nfilechooser 超时后先核验上传状态。",
            tool_name="read_skill_content",
        )
        self.assertEqual(result.status, "succeeded")

    def test_real_timeout_still_requires_reconciliation(self):
        result = adapt_mcp_tool_result(
            "错误: TimeoutError: page.goto: Timeout 30000ms exceeded",
            tool_name="execute_skill_script",
        )
        self.assertEqual(result.status, "unknown")


class LangChainStreamCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_create_agent_updates_are_counted_by_initial_and_resume_paths(self):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.tools import tool

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        @tool
        def cart_snapshot() -> str:
            """Return an observed empty cart without creating anything."""
            return '{"cart_count": 0}'

        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "cart_snapshot", "args": {}, "id": "cart-call-1", "type": "tool_call"
            }]),
            AIMessage(content="购物车为空，已核验。"),
        ])
        graph = create_agent(model, tools=[cart_snapshot])
        updates = [event async for event in graph.astream(
            {"messages": [HumanMessage(content="核验购物车")]}, stream_mode="updates"
        )]
        # 用实际框架产生的节点更新，检查两个生产流分支的条件，而非伪造旧 agent 节点。
        source = Path(__file__).with_name("agent_loop_view.py").read_text()
        predicates = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp):
                expr = ast.unparse(node.test)
                if "node_name" in expr and "agent" in expr and "node_output" in expr:
                    predicates.append(compile(ast.Expression(node.test), "stream-node", "eval"))
        self.assertEqual(len(predicates), 2)
        for predicate in predicates:
            tool_rounds = 0
            for event in updates:
                for name, output in event.items():
                    if eval(predicate, {"node_name": name, "node_output": output}):
                        tool_rounds += sum(bool(m.tool_calls) for m in output.get("messages", [])
                                           if hasattr(m, "tool_calls"))
            self.assertEqual(tool_rounds, 1)
