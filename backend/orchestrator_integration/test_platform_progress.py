"""Exercise the production middleware/graph/DB path, not only prompt snapshots."""
import asyncio
import json
import unittest
from types import SimpleNamespace

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from langgraph_integration.models import ChatSession, TokenUsageRecord
from .agent_runtime import AgentRuntime, create_or_resume_task, request_cancel
from .agent_loop_finalize import RuntimeSession, finalize_task_acceptance
from .browser_test_runtime import contract_for_goal, guard_command
from .models import AgentStep, AgentTask
from .runtime_progress import RuntimeProgressMiddleware, progress_event, account_model_call
from .task_commission import TaskCommission
from .tool_result import adapt_mcp_tool_result, classify_skill_output

TARGET = "https://example.test/custom-golf-pdp-174"
GOAL = (f"执行真实复杂测试验收：先查询知识库，再在 {TARGET} 检查必填文本为空时是否阻止加购。"
        "两组参数（颜色 Blue、文本 E2E_ALPHA；颜色 Red、文本 E2E_BETA），数量各1，核对购物车并截图，交付中文报告。不提交订单、不支付。")


class ProtocolAndContractTests(unittest.TestCase):
    def test_cart_delivery_adapter_keeps_order_and_multiple_product_tasks_on_their_full_plan(self):
        self.assertIsNone(contract_for_goal(GOAL.replace("不提交订单", "提交订单获取订单号")))
        self.assertIsNone(contract_for_goal(GOAL + "，另测 /another-pdp-20"))
        self.assertIsNotNone(contract_for_goal(GOAL))

    def test_explicit_empty_cart_page_finishes_rejected_rows_without_inventing_an_api_response(self):
        from .browser_test_runtime import observe, delivery_ready
        state = contract_for_goal(GOAL)
        state.update(knowledge_checked=True, page_checked=True, empty_check={"passed": True}, artifacts=[{"url": "/media/cart.png"}])
        state["rows"] = {r["key"]: {"result": "failed", "retryable": False, "operation_id": r["key"]} for r in state["expected_rows"]}
        result = {"operation_id": "page-cart", "data": {"browser_observation": {"page_url": "https://example.test/cart", "cart_empty": True,
                  "responses": [{"action": "navigation", "http_status": 200}]}}}
        observed = observe(state, tool_name="execute_skill_script", result=result)
        self.assertTrue(delivery_ready(observed))
        self.assertEqual(observed["cart_readback"]["observed_via"], "rendered_cart_page")
        self.assertNotIn("items_amount", observed["cart_readback"])
        state["rows"]["row-1"]["result"] = "passed"
        self.assertFalse(observe(state, tool_name="execute_skill_script", result=result).get("cart_readback"))

    def test_report_keeps_multiline_preview_inside_its_parameter_row(self):
        from .browser_test_runtime import report
        state = contract_for_goal(GOAL)
        state.update(empty_check={"passed": True}, cart_readback={"http_status": 200, "cart_count": 0})
        state["rows"] = {r["key"]: {"result": "failed", "operation_id": "observed", "preview": ["Preview:\n" + r["value"] + "|suffix"]} for r in state["expected_rows"]}
        output = report(state)
        self.assertEqual(len([line for line in output.splitlines() if line.startswith('|')]), 6)
        self.assertIn('预览：Preview: E2E_ALPHA\\|suffix', output)

    def test_browser_task_navigation_is_not_redirected_to_document_reader(self):
        from .builtin_tools.skill_tools import prefer_url_reader_instead
        self.assertIsNone(prefer_url_reader_instead("playwright-skill", "await page.goto('http://127.0.0.1/custom-golf-pdp-174')"))

    def test_goal_does_not_invent_a_row_from_empty_text_instruction(self):
        state = contract_for_goal(GOAL)
        self.assertEqual([r["value"] for r in state["expected_rows"]], ["E2E_ALPHA", "E2E_BETA"])
        self.assertTrue(state["requires_empty_check"])

    def test_multiline_stdout_retains_later_rejection(self):
        raw = 'STATE: {"active":["Red"]}\nPREVIEW: {"success":true}\nBODY: {"success":false,"error":{"message":"fields not exists:[attr_icon]"}}'
        result = classify_skill_output(text=raw, skill_name="playwright-skill", exit_code=0)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["execution_status"], "succeeded")
        self.assertEqual(result.data["business_status"], "failed")

    def test_runtime_notices_are_idempotent_and_keep_original_identity(self):
        original = {"status": "failed", "operation_id": "same-op", "data": {"raw_text": "HTTP 400"}, "retryable": False}
        value = json.dumps(original) + '\n\n[核对/重试] 不可重放。'
        first = adapt_mcp_tool_result(value, tool_name="execute_skill_script")
        for _ in range(4):
            first = adapt_mcp_tool_result(first.to_tool_text(), tool_name="execute_skill_script")
        self.assertEqual(first.operation_id, "same-op")
        self.assertEqual(first.data["raw_text"], "HTTP 400")
        self.assertEqual(len(first.data["runtime_notices"]), 1)

    def test_failed_row_blocks_replay_but_allows_remaining_data(self):
        state = contract_for_goal(GOAL)
        state["rows"] = {"row-1": {"result": "failed", "retryable": False}}
        state["last_inputs"] = ["E2E_ALPHA"]
        self.assertIn("E2E_BETA", guard_command(state, "await page.click('.add-cart')"))
        self.assertFalse(guard_command(state, "await page.fill('input', 'E2E_BETA'); await page.click('.add-cart')"))
        self.assertTrue(guard_command(state, "await page.goto('https://example.test/another-pdp-20')"))


class CountingModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        self.calls += 1
        if self.calls > len(self.responses):
            raise AssertionError("Runtime should have delivered the report without another provider call")
        return super()._generate(*args, **kwargs)


class PersistedGraphTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="progress-user", password="x")
        self.chat = ChatSession.objects.create(user=self.user, session_id="progress-session")
        self.commission = TaskCommission(task_id="progress-commission", zone_id="browser", playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}), auxiliary_skills=frozenset(), short_circuit=True,
            draft_writes=False, channel="light", max_tool_steps=100)
        self.task = create_or_resume_task(session=self.chat, user=self.user, project_id=None, goal=GOAL,
            commission=self.commission, auth_scope={"allow_actions": ["add_to_cart"], "deny_actions": ["pay", "submit_order"]},
            stage_plan=[{"stage_id": s} for s in ["fetch_conditions", "check_executable", "add_to_cart", "verify_deliver"]],
            lease_owner="progress-worker")

    async def test_real_graph_converges_to_failure_report_with_exact_progress_and_usage(self):
        @tool
        def knowledge_search(query: str) -> str:
            """Read the test procedure from the knowledge base."""
            return "[结果1] (来源: 商品中心操作要点xmind) 必填校验、预览、加购、回读购物车。"

        @tool
        def execute_skill_script(command: str) -> str:
            """Observe the browser operation for this test."""
            obs = {"page_url": TARGET, "ready": "complete", "input_values": [], "responses": [], "cart_writes_started": 0}
            if command == "page":
                obs["responses"] = [{"action": "navigation", "http_status": 200}]
            if command == "empty.click()":
                obs.update(validation_errors=["This item requires personalization"], empty_validation_blocked=True)
            if command in {"alpha", "beta"}:
                value = "E2E_ALPHA" if command == "alpha" else "E2E_BETA"
                obs.update(input_values=[value], cart_writes_started=1, previews=[f"Preview: {value}"],
                    responses=[{"action": "cart", "method": "POST", "http_status": 400, "success": False,
                        "retryable": False, "request_values": [value], "message": "fields not exists:[attr_icon]"}])
            if command == "cart":
                obs["responses"] = [{"action": "cart", "method": "GET", "http_status": 200, "cart_count": 0, "items_amount": "0.00"}]
            return json.dumps({"status": "succeeded", "operation_id": "op-" + command, "data": {
                "browser_observation": obs, "raw_text": "observed",
                "artifacts": [{"url": "/media/cart.png", "name": "cart.png"}]}})

        steps = [("knowledge_search", {"query": "定制加购"})] + [("execute_skill_script", {"command": x}) for x in ["page", "empty.click()", "alpha", "beta", "cart"]]
        model = CountingModel(responses=[AIMessage(content="", id=f"model-{i}",
            usage_metadata={"input_tokens": 10 * i, "output_tokens": i, "total_tokens": 11 * i},
            tool_calls=[{"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}]) for i, (name, args) in enumerate(steps, 1)])
        graph = create_agent(model, tools=[knowledge_search, execute_skill_script], middleware=[RuntimeProgressMiddleware(self.task.pk)])
        state = await graph.ainvoke({"messages": [HumanMessage(content=GOAL)]})
        self.assertEqual(model.calls, 6)
        answer = state["messages"][-1].content
        self.assertIn("商城加购用例未通过", answer)
        self.assertIn("E2E_ALPHA", answer); self.assertIn("E2E_BETA", answer)
        self.assertIn("未生成 cart_id", answer)
        snapshot = await sync_to_async(progress_event)(self.task.pk)
        self.assertEqual(snapshot["effective_config"]["used_tool_actions"], 6)
        self.assertEqual(snapshot["effective_config"]["completed_business_steps"], 4)
        self.assertEqual(await AgentStep.objects.filter(task_id=self.task.pk).acount(), 6)
        task = await AgentTask.objects.aget(pk=self.task.pk)
        self.assertEqual(task.budget["used_tokens"], 231)
        chat = await ChatSession.objects.aget(pk=self.chat.pk)
        self.assertEqual(chat.total_tokens, 231)
        self.assertEqual(await TokenUsageRecord.objects.filter(session_id=self.chat.session_id).acount(), 6)
        await sync_to_async(account_model_call)(self.task.pk, model.responses)
        self.assertEqual((await ChatSession.objects.aget(pk=self.chat.pk)).total_tokens, 231)
        runtime = AgentRuntime(lease_owner="progress-worker"); runtime.task = task
        acceptance, events = await sync_to_async(finalize_task_acceptance)(RuntimeSession(runtime=runtime, commission=self.commission))
        self.assertTrue(acceptance["execution_completed"])
        self.assertEqual(events[0]["effective_config"]["completed_business_steps"], 4)
        self.assertEqual(acceptance["test_result"], "failed")
        self.assertEqual((await AgentTask.objects.aget(pk=task.pk)).status, "completed")

    async def test_parallel_tools_count_actions_and_replayed_tool_call_is_not_reexecuted(self):
        calls = []
        @tool
        def knowledge_search(query: str) -> str:
            """Read a procedure."""
            calls.append(query)
            return "来源: 测试文档\n结果"
        model = CountingModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": str(i)}, "id": f"parallel-{i}", "type": "tool_call"} for i in range(3)]),
            AIMessage(content="查询完成。")])
        graph = create_agent(model, tools=[knowledge_search], middleware=[RuntimeProgressMiddleware(self.task.pk)])
        await graph.ainvoke({"messages": [HumanMessage(content="并行查询")]})
        snapshot = await sync_to_async(progress_event)(self.task.pk)
        self.assertEqual(snapshot["effective_config"]["used_tool_actions"], 3)
        from .runtime_progress import start_call
        replay = await sync_to_async(start_call)(self.task.pk, "parallel-0", "knowledge_search", {"query": "0"})
        self.assertTrue(replay.get("replay")); self.assertEqual(len(calls), 3)
        self.assertEqual((await sync_to_async(progress_event)(self.task.pk))["effective_config"]["used_tool_actions"], 3)

    async def test_stop_waits_for_inflight_then_settles_without_starting_another_tool(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        @tool
        async def slow_read() -> str:
            """Read a slow page without writing anything."""
            entered.set(); await finish.wait()
            return json.dumps({"status": "succeeded", "operation_id": "slow-op", "data": {"side_effect_possible": False}})
        model = CountingModel(responses=[AIMessage(content="", tool_calls=[{"name": "slow_read", "args": {}, "id": "slow-call", "type": "tool_call"}])])
        graph = create_agent(model, tools=[slow_read], middleware=[RuntimeProgressMiddleware(self.task.pk)])
        running = asyncio.create_task(graph.ainvoke({"messages": [HumanMessage(content="读取")] }))
        await asyncio.wait_for(entered.wait(), 3)
        cancellation = await sync_to_async(request_cancel)(await AgentTask.objects.aget(pk=self.task.pk))
        self.assertEqual(cancellation["status"], "cancelling")
        self.assertEqual(len(cancellation["in_flight"]), 1)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError): await running
        finish.set()
        for _ in range(100):
            task = await AgentTask.objects.aget(pk=self.task.pk)
            if task.status == "cancelled": break
            await asyncio.sleep(.01)
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(model.calls, 1)
        self.assertEqual(await AgentStep.objects.filter(task_id=task.pk).acount(), 1)
