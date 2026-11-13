"""跨轮路由：已授权浏览器任务 + 无操作关键词补充 + 知识库开启。

覆盖审计 KnownTaskFollowupTests 四项失败场景（FR08.4/08.5/08.6 / FR03.2）。
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from langgraph_integration.models import ChatSession
from orchestrator_integration.models import AgentTask
from orchestrator_integration.skill_zones import (
    ZONE_BROWSER,
    ZONE_KB_QA,
    SkillZone,
    correct_misrouted_zone,
    resolve_skill_zone,
    zone_skills,
)
from orchestrator_integration.turn_context import (
    TURN_SUPPLEMENT,
    PriorTaskContext,
    classify_turn_relation,
    load_open_task_context,
    routing_text_for_classify,
    should_preserve_execution,
)


def _seed_browser_task(*, username: str, session_id: str, release_lease: bool = True):
    User = get_user_model()
    user = User.objects.create_user(username=username, password="x")
    chat = ChatSession.objects.create(
        user=user, session_id=session_id, title="browser followup"
    )
    task = AgentTask.objects.create(
        session=chat,
        user=user,
        public_task_id=f"pub-{session_id}",
        goal=(
            "打开商品页上传图片后加购，不要下单不要支付 "
            "https://a.callie.cc/p1"
        ),
        status="running",
        current_stage="browser_interact",
        stage_plan=[
            {
                "stage_id": "browser_interact",
                "goal": "上传并加购",
                "tool_capabilities": [
                    "playwright-skill",
                    "execute_skill_script",
                    "read_skill_content",
                ],
                "acceptance": "cart_id",
                "auth_actions": ["upload", "add_to_cart"],
            }
        ],
        auth_scope={
            "allow_actions": ["upload", "add_to_cart"],
            "deny_actions": ["submit_order", "pay"],
        },
        task_scope={"raw_goal": "上传加购", "target_url": "https://a.callie.cc/p1"},
        lease_owner="" if release_lease else "other-worker",
        lease_expires_at=None,
    )
    return user, chat, task


class KnownTaskFollowupTests(TestCase):
    def test_supplement_without_continue_word_reuses_unfinished_task(self):
        from orchestrator_integration.agent_runtime import create_or_resume_task
        from orchestrator_integration.task_commission import TaskCommission

        user, chat, task = _seed_browser_task(
            username="mt-sup", session_id="sess-mt-sup"
        )
        prior = load_open_task_context(
            session_id="sess-mt-sup", user_id=user.id
        )
        self.assertIsNotNone(prior)
        msg = "后台用测试环境，站点选美站。"
        relation = classify_turn_relation(msg, prior)
        self.assertEqual(relation, TURN_SUPPLEMENT)
        self.assertTrue(should_preserve_execution(prior, relation))

        c = TaskCommission(
            task_id="ignored",
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="heavy",
        )
        resumed = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal=msg,
            commission=c,
            auth_scope=dict(task.auth_scope),
            turn_relation=relation,
            lease_owner="w-sup",
        )
        self.assertEqual(resumed.pk, task.pk)
        self.assertEqual(resumed.goal, task.goal)
        scope = resumed.task_scope or {}
        self.assertIn("supplement_message", scope)
        self.assertEqual((scope.get("constraints") or {}).get("environment"), "test")
        self.assertEqual((scope.get("constraints") or {}).get("site"), "us")

    def test_clarification_keeps_authorized_browser_execution_capability(self):
        from orchestrator_integration.builtin_tools import get_builtin_tools
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )

        user, chat, task = _seed_browser_task(
            username="mt-cap", session_id="sess-mt-cap"
        )
        prior = load_open_task_context(session_id="sess-mt-cap", user_id=user.id)
        msg = "后台用测试环境，站点用美站，按上面的方案继续。"
        relation = classify_turn_relation(msg, prior)
        self.assertTrue(should_preserve_execution(prior, relation))
        routing = routing_text_for_classify(msg, prior, relation)
        decision = classify_user_intent(routing)
        kb_policy = decide_kb_access_policy(routing, decision)
        zone = resolve_skill_zone(
            routing,
            decision,
            kb_policy,
            prior_stage=prior.current_stage,
            prior_auth=prior.auth_scope,
            turn_relation=relation,
        )
        self.assertEqual(zone.zone_id, ZONE_BROWSER)
        self.assertTrue(zone.allow_execute)

        # 模拟 kb 开启时的挂载门禁：preserve 时不得清空 allowlist / 卸掉 execute
        kb_qa_mode = True
        preserve = should_preserve_execution(prior, relation)
        mount_execute = zone.allow_execute or preserve or not kb_qa_mode
        allowlist = set(zone_skills(ZONE_BROWSER))
        if not zone.allow_execute and kb_qa_mode and not preserve:
            allowlist = set()
        tools = get_builtin_tools(
            user_id=user.id,
            project_id=1,
            allowed_skill_names=allowlist,
            allow_execute=mount_execute,
            include_meta_tools=mount_execute,
        )
        names = {getattr(t, "name", "") for t in tools}
        self.assertIn("execute_skill_script", names)
        self.assertIn("request_tool_expansion", names)
        self.assertTrue(mount_execute)
        self.assertTrue(bool(allowlist))
        # 对照：无 prior 的纯补参不得凭空授予执行
        alone = classify_turn_relation("后台用测试环境，站点选美站。", None)
        self.assertEqual(alone, "none")

    def test_internal_route_repair_is_available_when_goal_requires_browser(self):
        from orchestrator_integration.intent_router import IntentDecision

        prior = PriorTaskContext(
            task_pk=1,
            public_task_id="p1",
            goal="上传后加购 https://a.callie.cc/p1",
            current_stage="browser_interact",
            auth_scope={"allow_actions": ["upload", "add_to_cart"]},
        )
        msg = "站点选美站"
        relation = classify_turn_relation(msg, prior)
        bad = SkillZone(ZONE_KB_QA, frozenset(), allow_execute=False, reason="kb_qa")
        fixed = correct_misrouted_zone(
            msg,
            bad,
            IntentDecision(intents=set(), primary="qa"),
            prior_stage=prior.current_stage,
            prior_auth=prior.auth_scope,
            turn_relation=relation,
        )
        self.assertEqual(fixed.zone_id, ZONE_BROWSER)
        self.assertTrue(fixed.allow_execute)
        self.assertIn("prior_task", fixed.reason)

    def test_original_browser_task_cannot_complete_without_any_browser_action(self):
        from orchestrator_integration.agent_loop_finalize import (
            RuntimeSession,
            finalize_task_acceptance,
        )
        from orchestrator_integration.agent_runtime import AgentRuntime
        from orchestrator_integration.task_commission import TaskCommission

        user, chat, task = _seed_browser_task(
            username="mt-acc", session_id="sess-mt-acc"
        )
        # 本轮被误立项为 QA，仍不得让原浏览器任务 completed
        commission = TaskCommission(
            task_id=task.public_task_id,
            zone_id="kb_qa",
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="light",
            post_condition="",
        )
        commission.last_tool_results = []  # type: ignore[attr-defined]
        runtime = AgentRuntime(lease_owner="w-acc")
        runtime.task = task
        sess = RuntimeSession(
            runtime=runtime,
            commission=commission,
            stage_specs=[],
            auth_scope=dict(task.auth_scope),
        )
        acceptance, events = finalize_task_acceptance(sess)
        self.assertFalse(acceptance.get("ok"))
        self.assertTrue(
            any(
                "浏览器" in r or "证据" in r or "产物" in r
                for r in (acceptance.get("reasons") or [])
            )
        )
        task.refresh_from_db()
        self.assertNotEqual(task.status, "completed")
        self.assertFalse(
            any(e.get("type") == "task_completed" and e.get("status") == "completed" for e in events)
            and acceptance.get("ok")
        )


class MultiturnPositiveControlTests(TestCase):
    def test_full_goal_with_url_routes_browser(self):
        from orchestrator_integration.intent_router import classify_user_intent

        text = "打开 https://a.callie.cc/p1 上传图片后加购，不要支付"
        d = classify_user_intent(text)
        z = resolve_skill_zone(text, d, None)
        self.assertEqual(z.zone_id, ZONE_BROWSER)

    def test_env_supplement_alone_stays_kb_qa_without_prior_task(self):
        from orchestrator_integration.intent_router import classify_user_intent

        text = "后台用测试环境，站点选美站。"
        self.assertEqual(classify_turn_relation(text, None), "none")
        d = classify_user_intent(text)
        z = resolve_skill_zone(text, d, None)
        self.assertEqual(z.zone_id, ZONE_KB_QA)
        self.assertFalse(z.allow_execute)
