"""One persisted call ledger for live execution, resume, progress and delivery.

This adapts the existing AgentTask/AgentStep; it is not a second workflow engine.
The middleware owns call identity, so context propagation and SSE reconnects do
not create extra actions or leave the UI on an old in-memory task instance.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import replace

from asgiref.sync import sync_to_async
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Max
from django.utils import timezone
from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from . import browser_test_runtime as browser_tests
from .telemetry_context import get_telemetry_context, set_telemetry_context
from .tool_result import ToolResult, adapt_mcp_tool_result

_BACKGROUND_CALLS: set = set()
_NOOP_SCREENSHOT_DIR_READ = re.compile(
    r"\s*(?:(?:const|let|var)\s+[\w$]+\s*=\s*)?"
    r"process\.env\.SCREENSHOT_DIR\s*;?\s*",
    re.I,
)


def _command(args):
    commands = args.get("commands") or []
    if isinstance(commands, str):
        try:
            commands = json.loads(commands)
        except ValueError:
            commands = []
    return json.dumps(args.get("browser_plan") or {}, ensure_ascii=False) + "\n" + str(args.get("script") or args.get("command") or "") + "\n" + "\n".join(str(x.get("command") or x.get("script") or "") for x in commands if isinstance(x, dict))


def _sync_test_progress(task, state):
    facts = dict(task.facts or {})
    facts["browser_test"] = state
    facts["entry_url"] = state["target_url"]
    if state.get("page_checked"):
        facts["product_url"] = state["target_url"]
        facts["page_ok"] = True
    done = browser_tests.completed_nodes(state)
    facts["completed_stage_ids"] = done
    plan = dict(facts.get("task_plan") or {})
    for node in plan.get("nodes", []):
        node["status"] = "done" if node.get("node_id") in done else "pending"
    stages = list(task.stage_plan or [])
    stage_ids = [s.get("stage_id") for s in stages]
    current = next((s for s in stage_ids if s not in done), stage_ids[-1] if stage_ids else "verify_deliver")
    if task.current_stage != current:
        task.plan_version = int(task.plan_version or 0) + 1
    task.current_stage = current
    plan["current_node"] = current
    if plan:
        facts["task_plan"] = plan
    task.facts = facts
    budget = dict(task.budget or {})
    budget["completed_business_steps"] = len([s for s in stage_ids if s in done])
    task.budget = budget


@transaction.atomic
def _start_call_once(task_id, call_id, name, args):
    from .models import AgentTask, AgentStep
    from .agent_runtime import consume_budget, append_event

    task = AgentTask.objects.select_for_update().get(pk=task_id)
    previous = AgentStep.objects.filter(task=task, step_id=call_id).first()
    if previous:
        return {"replay": previous.structured_result or {"status": previous.status}, "precharged": False}
    if task.status in {"completed", "failed", "cancelled", "cancelling", "blocked"}:
        reason = "任务已阻塞" if task.status == "blocked" else "任务已结束或正在取消"
        return {"reject": f"{reason}，不启动新的动作。", "precharged": False}
    latest_step = AgentStep.objects.filter(task=task).aggregate(value=Max("step_number"))["value"] or 0
    # `current_step` can lag after an interrupted write or be advanced by
    # budget accounting. The persisted ledger is authoritative for numbering.
    number = max(int(task.current_step or 0), int(latest_step)) + 1
    state = (task.facts or {}).get("browser_test") or browser_tests.contract_for_goal(task.goal)
    plan_rejection = ""
    plan_arg = args.get("browser_plan") or {}
    if state and plan_arg.get("action") == "run_form_test":
        plan_rejection = browser_tests.plan_precondition_error(state, plan_arg)
        expected = {row["key"]: row for row in state["expected_rows"]}
        # The compiled path shares the existing call ledger and row identity.
        # Never replay a completed or uncertain write through a new plan call.
        prior_plans = AgentStep.objects.filter(task=task, tool_name=name, tool_input__browser_plan__action="run_form_test")
        unresolved = prior_plans.filter(status__in=["running", "unknown"]).exists()
        completed = state.get("rows", {})
        plan_rows = plan_arg.get("rows") if isinstance(plan_arg.get("rows"), list) else []
        invalid = unresolved or any(
            not isinstance(row, dict)
            or row != expected.get(row.get("key"))
            or row.get("key") in completed
            for row in plan_rows
        )
        if invalid and not plan_rejection:
            plan_rejection = "计划含已执行、未知状态或不匹配的参数行；先核验已有结果，仅修复剩余节点。"
    rejected = plan_rejection or (browser_tests.guard_command(state, _command(args)) if state and name == "execute_skill_script" else "")
    precharged = not bool(args.get("commands"))
    if precharged:
        ok, reason = consume_budget(task, tool_steps=1)
        if not ok:
            rejected = reason
    AgentStep.objects.create(task=task, step_number=number, step_id=call_id, operation_id=call_id,
                             status="running", tool_name=name, tool_input=args, stage_id=task.current_stage or "")
    task.current_step = max(int(task.current_step or 0), number)
    if state:
        facts = dict(task.facts or {}); facts["browser_test"] = state; task.facts = facts
    task.save(update_fields=["current_step", "facts", "updated_at"])
    append_event(task, "step_started", step_id=call_id, status="running", payload={"tool_name": name})
    return {"reject": rejected, "precharged": precharged, "stage": task.current_stage}


def _is_step_allocation_conflict(exc):
    message = str(exc).lower()
    unique_step = (
        "unique constraint failed: agent_step.task_id, agent_step.step_number" in message
        or ("agent_step" in message and "step_number" in message
            and ("unique" in message or "duplicate" in message))
    )
    sqlite_busy = "locked" in message or "database is busy" in message
    return unique_step or sqlite_busy


def start_call(task_id, call_id, name, args):
    """Allocate a per-task step safely when parallel calls share SQLite."""
    for attempt in range(4):
        try:
            return _start_call_once(task_id, call_id, name, args)
        except (IntegrityError, OperationalError) as exc:
            if attempt >= 3 or not _is_step_allocation_conflict(exc):
                raise
            # The failed atomic transaction has rolled back, so re-read the
            # task and the maximum persisted step on the next attempt.
            time.sleep(0.02 * (attempt + 1))


@transaction.atomic
def finish_call(task_id, call_id, name, args, result, duration_ms):
    from .models import AgentTask, AgentStep
    from .agent_runtime import advance_task_stage, append_event, finalize_cancel

    task = AgentTask.objects.select_for_update().get(pk=task_id)
    step = AgentStep.objects.filter(task=task, step_id=call_id).first()
    if not step or step.status != "running":
        return result
    result = dict(result)
    result["duration_ms"] = duration_ms
    data = dict(result.get("data") or {})
    obs = data.get("browser_observation")
    if isinstance(obs, dict):
        writes = obs.get("cart_writes_started", 0)
        responses = [x for x in obs.get("responses", []) if x.get("action") == "cart" and x.get("method") != "GET"]
        data["side_effect_possible"] = bool(writes > len(responses))
    result["data"] = data
    facts = dict(task.facts or {})
    script = str(args.get("script") or args.get("command") or "").strip()
    noop_probe = bool(
        name == "execute_skill_script"
        and not args.get("commands")
        and _NOOP_SCREENSHOT_DIR_READ.fullmatch(script)
    )
    loop_guard_blocked = False
    previous_probe = facts.get("last_noop_screenshot_dir_probe") or {}
    if noop_probe:
        same_probe = (
            previous_probe.get("command") == script
        )
        repeat_count = int(previous_probe.get("repeat_count") or 0) + 1 if same_probe else 1
        facts["last_noop_screenshot_dir_probe"] = {
            "command": script,
            "repeat_count": repeat_count,
        }
        if repeat_count >= 2:
            data["loop_guard"] = "repeated_no_progress_probe"
            data["next_action"] = (
                "SCREENSHOT_DIR 已读取过；复用第一次结果，禁止再次调用同一查询，继续页面验证。"
            )
            result["data"] = data
            result["summary"] = (
                "重复的 SCREENSHOT_DIR 查询已被识别；复用第一次结果并继续任务。"
            )
        if repeat_count >= 3:
            loop_guard_blocked = True
            result["status"] = "failed"
            result["error_code"] = "no_progress_repeated_probe"
            result["summary"] = (
                "同一 SCREENSHOT_DIR 只读探测已连续执行三次。任务已阻塞；"
                "用户继续后复用第一次结果，不要再次读取该变量。"
            )
    else:
        facts.pop("last_noop_screenshot_dir_probe", None)

    state = facts.get("browser_test")
    if state:
        state = browser_tests.observe(state, tool_name=name, result=result, command=_command(args))
        notice = browser_tests.directive(state)
        if notice:
            data["next_action"] = notice
        _sync_test_progress(task, state)
    elif not loop_guard_blocked:
        # Existing UI/API plans still use the existing acceptance predicates.
        advance_task_stage(task, tool_results=[json.dumps(result, ensure_ascii=False)])
    if loop_guard_blocked:
        task.status = "blocked"
        task.error_message = "no_progress_loop: repeated SCREENSHOT_DIR probe"
    task.save(update_fields=["facts", "budget", "current_stage", "plan_version", "updated_at"])
    if state and task.current_stage != "fetch_conditions":
        from .task_orchestrator.node_runtime import apply_remount_to_context
        apply_remount_to_context(["playwright-skill"])
    step.status = {"needs_confirmation": "awaiting_approval"}.get(result.get("status"), result.get("status") or "unknown")
    step.operation_id = result.get("operation_id") or call_id
    step.structured_result = result
    step.tool_output_summary = str(result.get("summary") or "")[:2000]
    step.duration_ms = duration_ms
    step.evidence = {**(result.get("evidence") or {}), "tool_call_id": call_id, "output_ref": result.get("output_ref") or ""}
    step.save()
    append_event(task, "step_result", step_id=call_id, status=step.status,
                 payload={"tool_name": name, "operation_id": step.operation_id, "stage": task.current_stage})
    if loop_guard_blocked:
        AgentTask.objects.filter(pk=task.pk).update(
            status="blocked", error_message=task.error_message
        )
        append_event(
            task,
            "task_blocked",
            status="blocked",
            step_id=call_id,
            payload={"blocker_code": "no_progress_repeated_probe"},
        )
    if task.status == "cancelling":
        finalize_cancel(task)
    return result


def progress_event(task_id):
    from .models import AgentTask
    from .task_state_closure import public_progress_view

    task = AgentTask.objects.get(pk=task_id)
    budget = task.budget or {}
    view = public_progress_view(task)
    return {
        "type": "task_status",
        "task_id": task.public_task_id or str(task.pk),
        "status": task.status,
        "payload": {
            "stage": task.current_stage,
            "stage_plan": task.stage_plan,
            "acceptance": task.acceptance,
            "progress_view": view,
            "incomplete": view.get("incomplete"),
            "label": view.get("label"),
        },
        "effective_config": {
            k: budget.get(k, 0)
            for k in (
                "expected_business_steps",
                "completed_business_steps",
                "max_tool_actions",
                "used_tool_actions",
                "config_version",
            )
        },
    }


@transaction.atomic
def prepare_delivery(task_id):
    from .models import AgentTask
    task = AgentTask.objects.select_for_update().get(pk=task_id)
    state = (task.facts or {}).get("browser_test")
    if task.status in {"cancelling", "cancelled", "failed", "completed"} or not state or not browser_tests.delivery_ready(state):
        return None
    state = dict(state)
    state["report_prepared"] = True
    state["report_content"] = browser_tests.report(state)
    _sync_test_progress(task, state)
    task.save(update_fields=["facts", "budget", "current_stage", "plan_version", "updated_at"])
    return state["report_content"]


@transaction.atomic
def account_model_call(task_id, messages):
    from .models import AgentTask
    from langgraph_integration.models import ChatSession, TokenUsageRecord
    task = AgentTask.objects.select_for_update().get(pk=task_id)
    budget = dict(task.budget or {})
    seen = list(budget.get("accounted_model_messages") or [])
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        message_id = str(message.id or "")
        usage = message.usage_metadata
        if not usage or not message_id or message_id in seen:
            continue
        seen.append(message_id)
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache = int((usage.get("input_token_details") or {}).get("cache_read") or 0)
        budget["used_tokens"] = int(budget.get("used_tokens") or 0) + input_tokens + output_tokens
        if task.session_id:
            ChatSession.objects.filter(pk=task.session_id).update(
                total_input_tokens=F("total_input_tokens") + input_tokens,
                total_output_tokens=F("total_output_tokens") + output_tokens,
                total_tokens=F("total_tokens") + input_tokens + output_tokens,
                total_cache_read_tokens=F("total_cache_read_tokens") + cache)
            TokenUsageRecord.objects.create(user_id=task.user_id, project_id=task.project_id,
                session_id=task.session.session_id, input_tokens=input_tokens, output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens, cache_read_tokens=cache)
    budget["accounted_model_messages"] = seen
    task.budget = budget
    task.save(update_fields=["budget", "updated_at"])


class RuntimeProgressMiddleware(AgentMiddleware):
    def __init__(self, task_id):
        self.task_id = task_id
        self.context = get_telemetry_context()

    def _context(self, call_id, precharged, stage=""):
        ctx = replace(self.context, runtime_task_id=self.task_id,
                      active_tool_call_id=call_id, budget_precharged=precharged, current_stage=stage)
        set_telemetry_context(ctx)

    def _finish(self, call, result, started):
        tr = adapt_mcp_tool_result(result.content, tool_name=call["name"]).to_dict()
        tr = finish_call(self.task_id, call["id"], call["name"], call.get("args") or {}, tr, round((time.monotonic() - started) * 1000))
        return result.model_copy(update={"content": json.dumps(tr, ensure_ascii=False)})

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        started = time.monotonic()
        info = start_call(self.task_id, call["id"], call["name"], call.get("args") or {})
        self._context(call["id"], info["precharged"], info.get("stage", ""))
        if info.get("replay"):
            return ToolMessage(content=json.dumps(info["replay"], ensure_ascii=False), tool_call_id=call["id"], name=call["name"])
        try:
            result = self._rejected(call, info["reject"]) if info.get("reject") else handler(request)
        except Exception as error:
            self._finish(call, self._unknown(call, error), started)
            raise
        return self._finish(call, result, started)

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        started = time.monotonic()
        info = await sync_to_async(start_call, thread_sensitive=True)(self.task_id, call["id"], call["name"], call.get("args") or {})
        self._context(call["id"], info["precharged"], info.get("stage", ""))
        if info.get("replay"):
            return ToolMessage(content=json.dumps(info["replay"], ensure_ascii=False), tool_call_id=call["id"], name=call["name"])
        if info.get("reject"):
            return await sync_to_async(self._finish, thread_sensitive=True)(call, self._rejected(call, info["reject"]), started)
        work = asyncio.create_task(handler(request))
        _BACKGROUND_CALLS.add(work)
        try:
            result = await asyncio.shield(work)
        except asyncio.CancelledError:
            async def settle():
                try:
                    result = await work
                    await sync_to_async(self._finish, thread_sensitive=True)(call, result, started)
                except Exception as error:
                    await sync_to_async(self._finish, thread_sensitive=True)(call, self._unknown(call, error), started)
                finally:
                    _BACKGROUND_CALLS.discard(work)
            observer = asyncio.create_task(settle())
            _BACKGROUND_CALLS.add(observer)
            observer.add_done_callback(_BACKGROUND_CALLS.discard)
            raise
        except Exception as error:
            _BACKGROUND_CALLS.discard(work)
            await sync_to_async(self._finish, thread_sensitive=True)(call, self._unknown(call, error), started)
            raise
        else:
            _BACKGROUND_CALLS.discard(work)
            return await sync_to_async(self._finish, thread_sensitive=True)(call, result, started)

    @staticmethod
    def _unknown(call, error):
        tr = ToolResult(status="unknown", operation_id=call["id"], error_code="tool_exception",
                        data={"side_effect_possible": call["name"] == "execute_skill_script"}, summary=str(error)[:500])
        return ToolMessage(content=tr.to_tool_text(), tool_call_id=call["id"], name=call["name"])

    @staticmethod
    def _rejected(call, reason):
        tr = ToolResult(status="skipped", operation_id=call["id"], error_code="test_row_already_observed",
                        data={"next_action": reason}, summary=reason)
        return ToolMessage(content=tr.to_tool_text(), tool_call_id=call["id"], name=call["name"])

    def wrap_model_call(self, request, handler):
        report = prepare_delivery(self.task_id)
        if report:
            return ModelResponse(result=[AIMessage(content=report, additional_kwargs={"runtime_report": True})])
        response = handler(self._with_directive(request))
        account_model_call(self.task_id, response.result)
        return response

    async def awrap_model_call(self, request, handler):
        report = await sync_to_async(prepare_delivery, thread_sensitive=True)(self.task_id)
        if report:
            return ModelResponse(result=[AIMessage(content=report, additional_kwargs={"runtime_report": True})])
        updated = await sync_to_async(self._with_directive, thread_sensitive=True)(request)
        response = await handler(updated)
        await sync_to_async(account_model_call, thread_sensitive=True)(self.task_id, response.result)
        return response

    def _with_directive(self, request):
        from .models import AgentTask
        task = AgentTask.objects.get(pk=self.task_id)
        state = (task.facts or {}).get("browser_test")
        hint = "\n".join(filter(None, [browser_tests.directive(state), browser_tests.form_execution_hint(state)])) if state else ""
        if not hint:
            return request
        text = str(request.system_message.content or "") if request.system_message else ""
        return request.override(system_message=SystemMessage(content=text + "\n\n当前任务运行态（非新任务）：" + hint))
