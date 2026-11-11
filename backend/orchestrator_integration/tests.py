import os
import tempfile
import time
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.test import TestCase
from django.test.utils import override_settings

from . import agent_loop_view
from .agent_loop_view import (
    _extract_linked_image_urls,
    _is_linked_image_url_allowed,
    _normalize_uploaded_image_base64_list,
    _prepare_agent_loop_human_message,
)
from .builtin_tools.skill_tools import (
    _build_skill_artifacts_dir,
    _collect_skill_artifacts,
    _build_skill_screenshots_dir,
    _finalize_skill_result,
    _prepare_skill_screenshots_dir,
    _SKILL_DIR_STALE_SECONDS,
    _sanitize_runtime_path_segment,
)
from .builtin_tools.output_sanitizer import strip_terminal_control_sequences
from .middleware_config import get_user_friendly_llm_error, _model_retry_should_retry
from projects.models import Project, ProjectMember
from requirements.models import DocumentImage, RequirementDocument


class LLMFriendlyErrorTests(SimpleTestCase):
    def test_model_cooldown_error_returns_friendly_payload(self):
        exc = Exception(
            "Error code: 429 - {'error': {'code': 'model_cooldown', 'message': 'All credentials for model coder-model are cooling down', 'model': 'coder-model', 'reset_seconds': 27211, 'reset_time': '7h33m31s'}}"
        )

        result = get_user_friendly_llm_error(exc)

        if result is None:
            raise AssertionError("expected friendly error payload")
        self.assertEqual(result["status_code"], 429)
        self.assertEqual(result["error_code"], "model_cooldown")
        self.assertEqual(result["model"], "coder-model")
        self.assertEqual(result["reset_seconds"], 27211)
        self.assertEqual(result["reset_time"], "7h33m31s")
        self.assertIn("coder-model", result["message"])
        self.assertIn("7h33m31s", result["message"])

    def test_generic_rate_limit_error_returns_friendly_payload(self):
        exc = Exception("HTTP 429 Too Many Requests")

        result = get_user_friendly_llm_error(exc)

        if result is None:
            raise AssertionError("expected friendly error payload")
        self.assertEqual(result["status_code"], 429)
        self.assertEqual(result["error_code"], "rate_limit")
        self.assertEqual(result["message"], "当前模型服务请求过于频繁，请稍后重试。")

    def test_model_cooldown_error_will_not_retry(self):
        exc = Exception(
            "Error code: 429 - {'error': {'code': 'model_cooldown', 'message': 'All credentials for model coder-model are cooling down', 'model': 'coder-model', 'reset_seconds': 27211, 'reset_time': '7h33m31s'}}"
        )

        self.assertFalse(_model_retry_should_retry(exc))

    def test_cooling_down_text_without_code_still_maps_to_model_cooldown(self):
        exc = Exception(
            "RateLimitError: provider says model service is cooling down, retry-after: 6m0s"
        )

        result = get_user_friendly_llm_error(exc)

        if result is None:
            raise AssertionError("expected friendly cooldown payload")
        self.assertEqual(result["status_code"], 429)
        self.assertEqual(result["error_code"], "model_cooldown")
        self.assertIn("冷却中", result["message"])

    def test_unsupported_model_is_friendly_and_not_retried(self):
        exc = Exception(
            "Error code: 400 - {'error': {'message': 'model `qwen3-max-2026-01-23` is not supported.', "
            "'type': 'invalid_request_error', 'param': '', 'code': 'invalid_parameter_error'}}"
        )

        result = get_user_friendly_llm_error(exc)

        if result is None:
            raise AssertionError("expected friendly unsupported-model payload")
        self.assertEqual(result["error_code"], "model_unsupported")
        self.assertEqual(result["model"], "qwen3-max-2026-01-23")
        self.assertIn("LLM 配置", result["message"])
        self.assertFalse(_model_retry_should_retry(exc))

    def test_ordinary_bad_request_is_not_unsupported_model(self):
        self.assertIsNone(get_user_friendly_llm_error(Exception("Error code: 400 - invalid tool schema")))


class LinkedImageUrlExtractionTests(SimpleTestCase):
    def test_extract_plain_http_url_stops_before_chinese_description(self):
        text = "请看 https://localhost:8080/a.png，准备注册信息：用户名testuser010、密码abcdef123"

        self.assertEqual(_extract_linked_image_urls(text), ["https://localhost:8080/a.png"])

    def test_plain_page_url_is_not_treated_as_image(self):
        text = "https://test-design.local.bomiv.com/#/home 这是测试地址"

        self.assertEqual(_extract_linked_image_urls(text), [])

    def test_extract_markdown_image_url_trims_wrapping_punctuation(self):
        text = "参考截图 ![image](https://example.com/demo.png)，然后继续分析"

        self.assertEqual(
            _extract_linked_image_urls(text),
            ["https://example.com/demo.png"],
        )

    def test_extract_invalid_unicode_netloc_does_not_raise(self):
        text = "异常链接 https://localhost:8080/b.jpg：准备注册信息：用户名testuser014"

        self.assertEqual(_extract_linked_image_urls(text), ["https://localhost:8080/b.jpg"])

    def test_extract_plain_http_url_stops_before_ascii_comma_description(self):
        text = "Open http://localhost:8080/c.png,then fill the registration form"

        self.assertEqual(_extract_linked_image_urls(text), ["http://localhost:8080/c.png"])

    def test_extract_plain_http_url_stops_before_closing_parenthesis_text(self):
        text = "查看截图 https://example.com/demo.png)后继续分析"

        self.assertEqual(
            _extract_linked_image_urls(text),
            ["https://example.com/demo.png"],
        )

    def test_allowlist_check_rejects_invalid_url_without_raising(self):
        with patch.object(
            agent_loop_view, "_LINKED_IMAGE_URL_ALLOWLIST", {"example.com"}
        ):
            self.assertFalse(
                _is_linked_image_url_allowed(
                    "https://localhost:8080：准备注册信息：用户名testuser014"
                )
            )


class UploadedImageNormalizationTests(SimpleTestCase):
    def test_normalize_uploaded_images_merges_legacy_and_array_fields(self):
        result = _normalize_uploaded_image_base64_list(
            ["img-a", " img-b ", "", "img-a"],
            "img-c",
        )

        self.assertEqual(result, ["img-a", "img-b", "img-c"])

    def test_normalize_uploaded_images_accepts_legacy_single_image_only(self):
        result = _normalize_uploaded_image_base64_list(None, " legacy-img ")

        self.assertEqual(result, ["legacy-img"])


class AgentLoopRequirementImageMessageTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.override_media = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)

        self.user = get_user_model().objects.create_user(
            username="agent-loop-user",
            password="password123",
        )
        self.project = Project.objects.create(
            name="Agent Loop Image Project",
            creator=self.user,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role="member",
        )
        self.document = RequirementDocument.objects.create(
            project=self.project,
            title="Requirement With Images",
            document_type="docx",
            uploader=self.user,
            has_images=True,
            image_count=1,
        )
        DocumentImage.objects.create(
            document=self.document,
            image_id="img_000",
            order=0,
            content_type="image/png",
            file_size=3,
            image_file=SimpleUploadedFile(
                "img-000.png",
                b"png",
                content_type="image/png",
            ),
        )

    def test_prepare_agent_loop_human_message_rewrites_requirement_placeholders(self):
        message = (
            "请分析以下需求\n\n"
            "![图片](docimg://img_000)\n\n"
            f"(这些需求模块来源于需求文档ID: {self.document.id})"
        )

        human_message_content, additional_kwargs, display_message = async_to_sync(
            _prepare_agent_loop_human_message
        )(
            message,
            project=self.project,
            supports_vision=False,
            uploaded_images_base64=[],
        )

        expected_url = (
            f"/api/requirements/documents/{self.document.id}/images/img_000/"
        )
        self.assertEqual(human_message_content, display_message)
        self.assertIn(expected_url, display_message)
        self.assertEqual(
            additional_kwargs["requirement_document_id"], str(self.document.id)
        )

    def test_prepare_agent_loop_human_message_attaches_requirement_images_for_vision(self):
        message = (
            "请分析以下需求\n\n"
            "![图片](docimg://img_000)\n\n"
            f"(这些需求模块来源于需求文档ID: {self.document.id})"
        )

        human_message_content, additional_kwargs, display_message = async_to_sync(
            _prepare_agent_loop_human_message
        )(
            message,
            project=self.project,
            supports_vision=True,
            uploaded_images_base64=[],
        )

        self.assertIsInstance(human_message_content, list)
        self.assertEqual(human_message_content[0]["type"], "text")
        self.assertIn(
            f"/api/requirements/documents/{self.document.id}/images/img_000/",
            human_message_content[0]["text"],
        )
        self.assertEqual(human_message_content[1]["type"], "image_url")
        self.assertTrue(
            human_message_content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(
            additional_kwargs["requirement_document_id"], str(self.document.id)
        )
        self.assertEqual(additional_kwargs["image_source"], "requirement_document")
        self.assertIn(
            f"/api/requirements/documents/{self.document.id}/images/img_000/",
            display_message,
        )


class SkillScreenshotDirectoryTests(SimpleTestCase):
    def test_sanitize_runtime_path_segment_blocks_path_traversal(self):
        self.assertEqual(
            _sanitize_runtime_path_segment("../case/89", "_default"),
            "__case_89",
        )

    def test_build_skill_screenshots_dir_uses_runtime_media_root(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root):
                screenshots_dir = _build_skill_screenshots_dir(1, "89")

        self.assertTrue(screenshots_dir.endswith("skill_runtime/screenshots/1/89"))
        self.assertNotIn("/skills/1/11/", screenshots_dir)

    def test_build_skill_screenshots_dir_keeps_path_inside_media_root(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root):
                screenshots_dir = _build_skill_screenshots_dir(1, "../case/89")

        self.assertTrue(screenshots_dir.startswith(temp_media_root))
        self.assertNotIn("..", screenshots_dir)

    def test_prepare_skill_screenshots_dir_clears_idle_dir(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root):
                screenshots_dir = _prepare_skill_screenshots_dir(1, "89")
                stale_file = os.path.join(screenshots_dir, "old.png")
                with open(stale_file, "w", encoding="utf-8") as f:
                    f.write("old screenshot")

                # 目录仍新鲜：不清理，避免误删当前会话截图
                refreshed_dir = _prepare_skill_screenshots_dir(1, "89")
                self.assertEqual(refreshed_dir, screenshots_dir)
                self.assertTrue(os.path.exists(stale_file))

                # 子目录内文件也参与闲置判定：子目录内容变新则不清空
                sub_file = os.path.join(screenshots_dir, "sub", "recent.png")
                os.makedirs(os.path.dirname(sub_file), exist_ok=True)
                with open(sub_file, "w", encoding="utf-8") as f:
                    f.write("recent")
                refreshed_dir = _prepare_skill_screenshots_dir(1, "89")
                self.assertTrue(os.path.exists(stale_file))

                # 目录树闲置超过阈值后清理
                old = time.time() - _SKILL_DIR_STALE_SECONDS - 60
                os.utime(stale_file, (old, old))
                os.utime(sub_file, (old, old))
                refreshed_dir = _prepare_skill_screenshots_dir(1, "89")
                self.assertEqual(refreshed_dir, screenshots_dir)
                self.assertFalse(os.path.exists(stale_file))

    def test_build_skill_artifacts_dir_uses_runtime_media_root(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root):
                artifacts_dir = _build_skill_artifacts_dir(1, "session-1")

        self.assertTrue(artifacts_dir.endswith("skill_runtime/artifacts/1/session-1"))
        self.assertNotIn("/skills/1/11/", artifacts_dir)

    def test_collect_skill_artifacts_detects_named_generated_file(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root, MEDIA_URL="/media/"):
                skill_dir = os.path.join(temp_media_root, "skills", "1", "11")
                os.makedirs(skill_dir, exist_ok=True)
                generated_file = os.path.join(skill_dir, "order-payment-flow.drawio")
                with open(generated_file, "w", encoding="utf-8") as f:
                    f.write("<mxfile></mxfile>")

                artifacts = _collect_skill_artifacts(
                    "已帮你生成 draw.io 文件：order-payment-flow.drawio",
                    skill_dir=skill_dir,
                    artifacts_dir=os.path.join(temp_media_root, "skill_runtime", "artifacts", "1", "s1"),
                    artifacts_before={},
                )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["name"], "order-payment-flow.drawio")
        self.assertEqual(artifacts[0]["url"], "/media/skills/1/11/order-payment-flow.drawio")

    def test_finalize_skill_result_wraps_output_with_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_media_root:
            with override_settings(MEDIA_ROOT=temp_media_root, MEDIA_URL="/media/"):
                skill_dir = os.path.join(temp_media_root, "skills", "1", "11")
                os.makedirs(skill_dir, exist_ok=True)
                generated_file = os.path.join(skill_dir, "demo.drawio")
                with open(generated_file, "w", encoding="utf-8") as f:
                    f.write("<mxfile></mxfile>")

                wrapped = _finalize_skill_result(
                    "已生成文件 demo.drawio",
                    skill_dir=skill_dir,
                    artifacts_dir=os.path.join(temp_media_root, "skill_runtime", "artifacts", "1", "s1"),
                    artifacts_before={},
                )

        self.assertIn('"type": "file"', wrapped)
        self.assertIn('/media/skills/1/11/demo.drawio', wrapped)


class TerminalOutputSanitizerTests(SimpleTestCase):
    def test_strip_terminal_control_sequences_removes_ansi_color_codes(self):
        raw = "\x1b[32m✓\x1b[0m Browser closed"

        self.assertEqual(strip_terminal_control_sequences(raw), "✓ Browser closed")


class BehaviorMemoryTests(SimpleTestCase):
    def test_kb_priority_hint_forbids_skill_name_as_tool(self):
        hint = agent_loop_view._KB_PRIORITY_HINT
        self.assertIn("禁止把 Skill 名称", hint)
        self.assertIn("url-reader", hint)
        self.assertIn("由你按用户意图决定", hint)
        self.assertIn("user_confirmed=true", hint)
        self.assertIn("多工具顺序与组合", hint)
        self.assertIn("parallel=false", hint)
        self.assertIn("本轮意图路由", hint)
        self.assertIn("读不了局域网", hint)

    def test_mutating_skill_command_detection(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            is_mutating_skill_command,
        )

        self.assertTrue(
            is_mutating_skill_command(
                "python loca_stude_tools.py --action add_testcase --name x"
            )
        )
        self.assertTrue(
            is_mutating_skill_command(
                "python loca_stude_tools.py --action delete_module --id 1"
            )
        )
        self.assertFalse(
            is_mutating_skill_command(
                "python loca_stude_tools.py --action get_projects"
            )
        )
        self.assertFalse(is_mutating_skill_command("node scripts/read_url.js https://a.com"))

    def test_should_record_tool_behavior_skips_noise(self):
        from orchestrator_integration.behavior_memory import should_record_tool_behavior

        self.assertFalse(should_record_tool_behavior("knowledge_search"))
        self.assertFalse(should_record_tool_behavior("read_skill_content"))
        self.assertTrue(should_record_tool_behavior("execute_skill_script"))

    def test_sanitize_redacts_secrets(self):
        from orchestrator_integration.behavior_memory import sanitize_behavior_text

        text = sanitize_behavior_text(
            'python scripts/read_url.py --header "Cookie: session=abc123" --header "Authorization: Bearer tok"'
        )
        self.assertNotIn("abc123", text)
        self.assertNotIn("tok", text)
        self.assertIn("[REDACTED]", text)

    def test_looks_like_user_correction(self):
        from orchestrator_integration.behavior_memory import looks_like_user_correction

        self.assertTrue(looks_like_user_correction("不对，应该是 siteadmin.callie.com"))
        self.assertFalse(looks_like_user_correction("请帮我查一下生产地址"))

    def test_prefetch_formats_hits(self):
        from orchestrator_integration.behavior_memory import prefetch_user_behavior_context

        with patch(
            "orchestrator_integration.behavior_memory.UserBehaviorStore.search",
            return_value=[
                {
                    "event_type": "user_correction",
                    "score": 0.91,
                    "summary_text": "用户纠正: Callie 后台是 siteadmin.callie.com",
                    "metadata": {},
                }
            ],
        ):
            ctx = prefetch_user_behavior_context(1, "Callieus生产地址", top_k=3)
        self.assertIn("账号行为偏好", ctx)
        self.assertIn("siteadmin.callie.com", ctx)

    def test_rank_behavior_boosts_correction(self):
        from orchestrator_integration.behavior_memory import rank_behavior_hits

        ranked = rank_behavior_hits(
            [
                {"event_type": "tool_call", "score": 0.9, "summary_text": "t"},
                {"event_type": "user_correction", "score": 0.8, "summary_text": "c"},
            ]
        )
        self.assertEqual(ranked[0]["event_type"], "user_correction")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])


class AgentSmartnessTests(SimpleTestCase):
    """意图路由 / 引用冲突 / 输出压缩 / 写入确认 — 最小回归。"""

    class _FakeTool:
        def __init__(self, name):
            self.name = name

    def test_classify_qa_vs_write_vs_web(self):
        from orchestrator_integration.intent_router import (
            INTENT_QA,
            INTENT_WEB,
            INTENT_WRITE,
            classify_user_intent,
        )

        qa = classify_user_intent("Callieus 生产后台地址是什么")
        self.assertTrue(qa.pure_kb_qa)
        self.assertEqual(qa.primary, INTENT_QA)

        write = classify_user_intent("把上面用例保存入库")
        self.assertIn(INTENT_WRITE, write.intents)

        web = classify_user_intent("读取 https://example.com/docs 的内容")
        self.assertIn(INTENT_WEB, web.intents)

    def test_kb_policy_skip_chitchat_and_force_on_correction(self):
        from orchestrator_integration.intent_router import (
            INTENT_CHITCHAT,
            classify_user_intent,
            decide_kb_access_policy,
            filter_tools_by_intent,
        )

        chitchat = classify_user_intent("你好")
        self.assertIn(INTENT_CHITCHAT, chitchat.intents)
        policy = decide_kb_access_policy("你好", chitchat)
        self.assertTrue(policy.skip_prefetch)
        self.assertFalse(policy.force_kb)
        self.assertFalse(policy.allow_knowledge_search)

        meta_policy = decide_kb_access_policy("为什么你刚才没找到")
        self.assertTrue(meta_policy.skip_prefetch)
        self.assertEqual(meta_policy.reason, "meta")

        force = decide_kb_access_policy("不对，应该是 siteadmin.callie.com")
        self.assertTrue(force.force_kb)
        self.assertFalse(force.try_answer_cache)

        reask = decide_kb_access_policy("再查一下知识库")
        self.assertTrue(reask.force_kb)

        tools = [
            self._FakeTool("knowledge_search"),
            self._FakeTool("read_skill_content"),
            self._FakeTool("execute_skill_script"),
        ]
        names = [
            t.name
            for t in filter_tools_by_intent(
                tools, chitchat, kb_active=True, kb_policy=policy
            )
        ]
        self.assertNotIn("knowledge_search", names)
        self.assertNotIn("execute_skill_script", names)

    def test_qa_cache_format_and_source_extract(self):
        from orchestrator_integration.qa_answer_cache import (
            extract_sources_from_answer,
            format_cached_answer_context,
        )

        ctx = format_cached_answer_context(
            {
                "score": 0.91,
                "answer": "生产地址是 a.example.com（来源：《环境说明》）",
                "sources": ["环境说明"],
            }
        )
        self.assertIn("历史已答", ctx)
        self.assertIn("a.example.com", ctx)
        self.assertEqual(
            extract_sources_from_answer("见《环境说明》与《账号手册》"),
            ["环境说明", "账号手册"],
        )

    def test_filter_drops_execute_on_pure_qa(self):
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            filter_tools_by_intent,
        )

        tools = [
            self._FakeTool("knowledge_search"),
            self._FakeTool("read_skill_content"),
            self._FakeTool("execute_skill_script"),
            self._FakeTool("playwright_navigate"),
        ]
        decision = classify_user_intent("生产环境地址是什么")
        names = [t.name for t in filter_tools_by_intent(tools, decision)]
        self.assertIn("knowledge_search", names)
        self.assertIn("read_skill_content", names)
        self.assertNotIn("execute_skill_script", names)
        self.assertNotIn("playwright_navigate", names)

    def test_filter_keeps_execute_on_write(self):
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            filter_tools_by_intent,
        )

        tools = [
            self._FakeTool("knowledge_search"),
            self._FakeTool("execute_skill_script"),
            self._FakeTool("playwright_click"),
        ]
        decision = classify_user_intent("创建用例并保存到平台")
        names = [t.name for t in filter_tools_by_intent(tools, decision)]
        self.assertIn("execute_skill_script", names)
        self.assertNotIn("playwright_click", names)

    def test_write_intent_hint_has_playbook(self):
        from orchestrator_integration.intent_router import (
            build_intent_hint,
            classify_user_intent,
        )

        hint = build_intent_hint(classify_user_intent("保存用例入库"))
        self.assertIn("落库剧本", hint)
        self.assertIn("user_confirmed=true", hint)

    def test_web_intent_forbids_lan_hallucination(self):
        from orchestrator_integration.intent_router import (
            build_intent_hint,
            classify_user_intent,
        )

        hint = build_intent_hint(
            classify_user_intent(
                "读 http://192.168.12.216:8765/agent-test-report.html 并总结"
            )
        )
        self.assertIn("url-reader", hint)
        self.assertIn("无法访问局域网", hint)

    def test_prefetch_conflict_note_for_two_hosts(self):
        note = agent_loop_view._build_prefetch_conflict_note(
            [
                (
                    1,
                    {
                        "similarity_score": 0.9,
                        "content": "后台地址 https://siteadmin.callie.com",
                    },
                ),
                (
                    2,
                    {
                        "similarity_score": 0.88,
                        "content": "另一套后台 https://admin.bomiv.com",
                    },
                ),
            ]
        )
        self.assertIn("来源冲突", note)
        self.assertIn("callie.com", note)
        self.assertIn("bomiv.com", note)

    def test_citation_hint_present(self):
        self.assertIn("标注来源", agent_loop_view._CITATION_HINT)

    def test_compact_skill_output_truncates(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            compact_skill_tool_output,
        )

        long = "x" * 5000
        out = compact_skill_tool_output(long, max_chars=2500)
        self.assertLess(len(out), len(long))
        self.assertIn("已截断", out)

    def test_needs_confirmation_not_compacted_away(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            compact_skill_tool_output,
            _needs_write_confirmation_payload,
        )

        payload = _needs_write_confirmation_payload(
            skill_name="loca-stude",
            command="python t.py --action add_testcase",
        )
        self.assertEqual(compact_skill_tool_output(payload), payload)
        self.assertIn("needs_confirmation", payload)

    def test_mutating_detection_covers_ui_automation_actions(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            is_mutating_skill_command,
        )

        for cmd in (
            "python ui_automation_tools.py --action create_ui_module --name x",
            "python ui_automation_tools.py --action batch_create_elements --page_id 1",
            "python ui_automation_tools.py --action set_step_details --step_id 1",
            "python ui_automation_tools.py --action set_case_steps --testcase_id 1",
            "python whart_tools.py --action upload_screenshots --case_id 35",
            "python loca_stude_tools.py --action edit_testcase --project_id 2 --case_id 102",
            "python loca_stude_tools.py --action approve_review --id 1",
        ):
            self.assertTrue(is_mutating_skill_command(cmd), cmd)
        for cmd in (
            "python ui_automation_tools.py --action get_ui_modules --project_id 1",
            "python ui_automation_tools.py --action get_testcase --testcase_id 1",
            "python loca_stude_tools.py --action get_testcase_detail --project_id 2 --case_id 102",
            "python loca_stude_tools.py --action list_modules --project_id 2",
            "python ui_automation_tools.py --action execute_testcase --testcase_id 1",
            "python scripts/read_url.py http://192.168.1.1/a.html",
        ):
            self.assertFalse(is_mutating_skill_command(cmd), cmd)

    def test_platform_read_and_approval_keep_execute_tool(self):
        from orchestrator_integration.intent_router import (
            INTENT_PLATFORM,
            INTENT_WRITE,
            apply_intent_routing,
        )

        class _T:
            def __init__(self, name):
                self.name = name

        tools = [_T("read_skill_content"), _T("execute_skill_script")]
        for text, intent in (
            ("查看已有用例", INTENT_PLATFORM),
            ("项目里有哪些模块", INTENT_PLATFORM),
            ("确认", INTENT_WRITE),
            ("好的，写入吧", INTENT_WRITE),
        ):
            decision, kept, _, _policy = apply_intent_routing(text, tools)
            self.assertIn(intent, decision.intents, text)
            self.assertIn("execute_skill_script", [t.name for t in kept], text)

        decision, kept, _, _policy = apply_intent_routing("先生成一条给我看看", tools)
        self.assertNotIn("execute_skill_script", [t.name for t in kept])

        for text in (
            "把知识沉淀写入知识库",
            "执行 skill workorder-knowledge",
            "按文档执行 upsert_knowledge_extract",
            "执行测试用例",
            "把这条用例跑一下",
            "运行自动化用例",
        ):
            decision, kept, _, _policy = apply_intent_routing(text, tools)
            self.assertIn(
                "execute_skill_script",
                [t.name for t in kept],
                text,
            )

        # 执行用例：跳过 KB、裁掉 knowledge_search，并注入执行剧本
        tools_kb = [
            _T("knowledge_search"),
            _T("read_skill_content"),
            _T("execute_skill_script"),
        ]
        decision, kept, hint, policy = apply_intent_routing(
            "确认执行测试用例 35", tools_kb, kb_active=True
        )
        self.assertEqual(policy.reason, "testcase_execute")
        self.assertTrue(policy.skip_prefetch)
        self.assertFalse(policy.allow_knowledge_search)
        self.assertNotIn("knowledge_search", [t.name for t in kept])
        self.assertIn("execute_skill_script", [t.name for t in kept])
        self.assertIn("execute_testcase", hint)
        self.assertIn("禁止调用 knowledge_search", hint)

        # 未挂知识库时，纯问答也不裁执行工具
        _, kept, hint, _policy = apply_intent_routing(
            "帮我优化一下", tools, kb_active=False
        )
        self.assertIn("execute_skill_script", [t.name for t in kept])
        self.assertNotIn("未挂载 execute_skill_script", hint)

    def test_commands_accepts_json_string(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            get_skill_tools,
            normalize_commands_arg,
        )

        self.assertEqual(
            normalize_commands_arg('{"skill_name": "a", "command": "python x.py"}'),
            [{"skill_name": "a", "command": "python x.py"}],
        )
        with self.assertRaises(ValueError):
            normalize_commands_arg("not json")

        tools = {
            t.name: t
            for t in get_skill_tools(user_id=1, project_id=1, user_write_approved=False)
        }
        out = tools["execute_skill_script"].invoke(
            {
                "commands": '[{"skill_name": "whart-test", "command": "python whart_tools.py --action add_testcase --project_id 2"}]',
                "parallel": False,
            }
        )
        self.assertIn("needs_confirmation", out)

    def test_detect_user_write_approval(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            detect_user_write_approval,
        )

        for text in (
            "确认", "好的", "可以，写入吧", "ok", "同意写入", "直接保存，不用问我", "执行吧",
            "继续更新", "接着改", "剩下的也更新", "不要问我，直接写",
        ):
            self.assertTrue(detect_user_write_approval(text), text)
        for text in (
            "执行ID为35的测试用例，并生成UI自动化用例",
            "可以帮我执行用例35吗",
            "先不要写入",
            "先不要继续更新",
            "其他更新了什么",
            "取消",
            "帮我看看这个页面",
            "",
        ):
            self.assertFalse(detect_user_write_approval(text), text)

    def test_execute_testcase_path_skips_kb_and_keeps_tool(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            is_mutating_skill_command,
        )
        from orchestrator_integration.intent_router import (
            apply_intent_routing,
            decide_kb_access_policy,
            is_testcase_execute_intent,
        )

        class _T:
            def __init__(self, name):
                self.name = name

        tools = [
            _T("knowledge_search"),
            _T("read_skill_content"),
            _T("execute_skill_script"),
        ]
        for text in (
            "确认执行测试用例 35",
            "执行ID为35的测试用例，并生成UI自动化用例",
            "可以帮我执行用例35吗",
            "帮我跑一下用例 12",
            "执行用例#40",
        ):
            self.assertTrue(is_testcase_execute_intent(text), text)
            policy = decide_kb_access_policy(text)
            self.assertEqual(policy.reason, "testcase_execute", text)
            self.assertTrue(policy.skip_prefetch, text)
            self.assertFalse(policy.allow_knowledge_search, text)
            _, kept, hint, policy2 = apply_intent_routing(
                text, tools, kb_active=True
            )
            self.assertEqual(policy2.reason, "testcase_execute", text)
            self.assertIn("execute_skill_script", [t.name for t in kept], text)
            self.assertNotIn("knowledge_search", [t.name for t in kept], text)
            self.assertIn("execute_testcase", hint, text)

        self.assertFalse(
            is_mutating_skill_command(
                "python ui_automation_tools.py --action execute_testcase "
                "--testcase_id 35 --wait_result"
            )
        )
        self.assertTrue(
            is_mutating_skill_command(
                "python ui_automation_tools.py --action create_ui_module --name x"
            )
        )

    def test_skill_governance_blocks_cross_skill_and_execute_write(self):
        from orchestrator_integration.skill_governance import (
            reject_cross_skill_batch,
            reject_zone_privilege_escalation,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_EXECUTE,
            ZONE_PLATFORM,
            resolve_skill_zone,
        )
        from orchestrator_integration.builtin_tools.skill_tools import (
            detect_user_write_approval,
        )
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )

        # 跨亲和组批量拒绝
        cross = reject_cross_skill_batch(
            [
                {
                    "skill_name": "ui-automation",
                    "command": "python ui_automation_tools.py --action execute_testcase --testcase_id 1",
                },
                {
                    "skill_name": "workorder-knowledge",
                    "command": "python workorder_knowledge_tools.py --action upsert_knowledge",
                },
            ]
        )
        self.assertIsNotNone(cross)
        self.assertIn("亲和组", cross)

        # 执行区禁止 create（越权）
        priv = reject_zone_privilege_escalation(
            zone_id=ZONE_EXECUTE,
            skill_name="ui-automation",
            command="python ui_automation_tools.py --action create_ui_module --name x",
        )
        self.assertIsNotNone(priv)
        self.assertIn("执行区", priv)

        ok_run = reject_zone_privilege_escalation(
            zone_id=ZONE_EXECUTE,
            skill_name="ui-automation",
            command="python ui_automation_tools.py --action execute_testcase --testcase_id 1 --wait_result",
        )
        self.assertIsNone(ok_run)

        # 确认执行 ≠ 同意写入
        self.assertFalse(detect_user_write_approval("确认执行测试用例 35"))
        self.assertTrue(detect_user_write_approval("确认写入"))
        self.assertTrue(detect_user_write_approval("执行吧"))

        # 写区不再并集 workorder（沉淀走 ticket_kb）
        from orchestrator_integration.skill_catalog import reload_skill_catalog

        reload_skill_catalog()
        text = "确认写入平台用例"
        d = classify_user_intent(text)
        z = resolve_skill_zone(text, d, decide_kb_access_policy(text, d))
        self.assertEqual(z.zone_id, ZONE_PLATFORM)
        self.assertNotIn("workorder-knowledge", z.allowed_skills)
        self.assertIn("loca-stude", z.allowed_skills)

    def test_batch_runs_readonly_before_write_confirm(self):
        from orchestrator_integration.builtin_tools import skill_tools as st

        payload = st._needs_write_confirmation_payload(
            skill_name=None,
            command=None,
            pending_commands=[
                {
                    "skill_name": "ui-automation",
                    "command": "python ui_automation_tools.py --action create_ui_module --name x",
                }
            ],
            executed_readonly=[
                {
                    "index": 0,
                    "skill_name": "ui-automation",
                    "command": "python ui_automation_tools.py --action execute_testcase --testcase_id 35 --wait_result",
                    "result": "ok",
                }
            ],
        )
        self.assertIn("needs_confirmation", payload)
        self.assertIn("只读/执行类命令已先执行", payload)
        self.assertIn("executed_readonly", payload)
        self.assertIn("execute_testcase", payload)

    def test_model_cannot_self_confirm_write(self):
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools

        tools = {
            t.name: t
            for t in get_skill_tools(user_id=1, project_id=1, user_write_approved=False)
        }
        out = tools["execute_skill_script"].invoke(
            {
                "skill_name": "ui-automation",
                "command": "python ui_automation_tools.py --action create_ui_module --name x",
                "user_confirmed": True,
            }
        )
        self.assertIn("needs_confirmation", out)

        out = tools["execute_skill_script"].invoke(
            {
                "commands": [
                    {
                        "skill_name": "ui-automation",
                        "command": "python ui_automation_tools.py --action set_case_steps --testcase_id 1",
                    }
                ],
                "user_confirmed": True,
            }
        )
        self.assertIn("needs_confirmation", out)

    def test_prefer_url_reader_blocks_browser_use(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            prefer_url_reader_instead,
        )

        msg = prefer_url_reader_instead(
            "browser-use",
            'new_tab("http://192.168.12.216:8765/agent-test-report.html")',
        )
        self.assertIsNotNone(msg)
        self.assertIn("url-reader", msg)
        self.assertIn("read_url.py", msg)

    def test_prefer_url_reader_blocks_playwright_html_report(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            prefer_url_reader_instead,
        )

        msg = prefer_url_reader_instead(
            "playwright-skill",
            "node run.js \"await page.goto('http://192.168.12.216:8765/agent-test-x.html')\"",
        )
        self.assertIsNotNone(msg)
        self.assertIn("url-reader", msg)

        # 带 screenshot / waitForTimeout 仍应拦截（不是真正交互）
        msg2 = prefer_url_reader_instead(
            "playwright-skill",
            "node run.js \"await page.goto('http://x/agent-test.html'); "
            "await page.waitForTimeout(3000); await page.screenshot({path:'a.png'});\"",
        )
        self.assertIsNotNone(msg2)

        ok = prefer_url_reader_instead(
            "playwright-skill",
            'node run.js "await page.goto(\'https://example.com/login\'); await page.fill(\'#user\', \'a\')"',
        )
        self.assertIsNone(ok)

    def test_linked_image_url_not_glue_chinese(self):
        urls = agent_loop_view._extract_linked_image_urls(
            "http://192.168.12.216:8765/agent-test-report.html读这个网页"
        )
        self.assertEqual(urls, [])

        img = agent_loop_view._extract_linked_image_urls(
            "看图 https://cdn.example.com/a.png 然后总结"
        )
        self.assertEqual(img, ["https://cdn.example.com/a.png"])

    def test_sanitize_prefetch_query_strips_url(self):
        q = agent_loop_view.sanitize_prefetch_query(
            "http://192.168.12.216:8765/a.html读这个网页，总结后问我是否保存成用例"
        )
        self.assertNotIn("a.html", q)
        self.assertNotIn("8765", q)
        self.assertIn("192.168.12.216", q)
        self.assertIn("读这个网页", q)

        q = agent_loop_view.sanitize_prefetch_query("https://admin.ps.callie.cc 这个是什么地址")
        self.assertEqual(q, "admin.ps.callie.cc 这个是什么地址")

        q = agent_loop_view.sanitize_prefetch_query(
            "https://admin.ps.callie.cc/login", keep_hosts=False
        )
        self.assertEqual(q, "")

    def test_web_then_ask_save_not_write_primary(self):
        from orchestrator_integration.intent_router import (
            INTENT_WEB,
            INTENT_WRITE,
            classify_user_intent,
        )

        d = classify_user_intent(
            "http://192.168.12.216:8765/a.html读这个网页，总结后问我是否保存成用例"
        )
        self.assertIn(INTENT_WEB, d.intents)
        self.assertNotIn(INTENT_WRITE, d.intents)
        self.assertEqual(d.primary, INTENT_WEB)

    def test_skill_zones_partition_without_llm(self):
        from orchestrator_integration.intent_router import (
            apply_intent_routing,
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_EXECUTE,
            ZONE_KB_QA,
            ZONE_PLATFORM,
            ZONE_TICKET_KB,
            ZONE_WEB,
            resolve_skill_zone,
        )
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools

        class _T:
            def __init__(self, name):
                self.name = name

        tools = [
            _T("knowledge_search"),
            _T("read_skill_content"),
            _T("execute_skill_script"),
        ]

        # 金标 1：纯问答
        d = classify_user_intent("登录流程怎么测")
        policy = decide_kb_access_policy("登录流程怎么测", d)
        zone = resolve_skill_zone("登录流程怎么测", d, policy)
        self.assertEqual(zone.zone_id, ZONE_KB_QA)
        self.assertFalse(zone.allow_execute)

        _, kept, hint, _ = apply_intent_routing(
            "登录流程怎么测", tools, kb_active=True, skill_zone=zone
        )
        self.assertNotIn("execute_skill_script", [t.name for t in kept])
        self.assertIn("Skill 分区", hint)

        # 金标 2：沉淀知识库
        ticket = "把钉钉工单沉淀到知识库"
        d2 = classify_user_intent(ticket)
        policy2 = decide_kb_access_policy(ticket, d2)
        zone2 = resolve_skill_zone(ticket, d2, policy2)
        self.assertEqual(zone2.zone_id, ZONE_TICKET_KB)
        self.assertTrue(zone2.allow_execute)
        self.assertIn("workorder-knowledge", zone2.allowed_skills)
        _, kept2, hint2, _ = apply_intent_routing(
            ticket, tools, kb_active=True, skill_zone=zone2
        )
        self.assertIn("execute_skill_script", [t.name for t in kept2])
        self.assertIn("workorder-knowledge", hint2)

        # 金标 3：跑用例
        run = "确认执行测试用例 35"
        d3 = classify_user_intent(run)
        policy3 = decide_kb_access_policy(run, d3)
        zone3 = resolve_skill_zone(run, d3, policy3)
        self.assertEqual(zone3.zone_id, ZONE_EXECUTE)

        # 金标 4：读网页
        web = "读这个网页 https://example.com"
        d4 = classify_user_intent(web)
        zone4 = resolve_skill_zone(web, d4, decide_kb_access_policy(web, d4))
        self.assertEqual(zone4.zone_id, ZONE_WEB)

        # 金标 5：平台查询
        plat = "查看已有用例"
        d5 = classify_user_intent(plat)
        zone5 = resolve_skill_zone(plat, d5, decide_kb_access_policy(plat, d5))
        self.assertEqual(zone5.zone_id, ZONE_PLATFORM)
        self.assertIn("loca-stude", zone5.allowed_skills)

        # 服务端白名单：execute 区禁止 workorder-knowledge
        tools_map = {
            t.name: t
            for t in get_skill_tools(
                user_id=1,
                project_id=1,
                user_write_approved=True,
                allowed_skill_names=set(zone3.allowed_skills),
                allow_execute=True,
            )
        }
        blocked = tools_map["execute_skill_script"].invoke(
            {
                "skill_name": "workorder-knowledge",
                "command": "python x.py --action upsert_knowledge",
                "user_confirmed": True,
            }
        )
        self.assertIn("不在本轮分区白名单", blocked)

        # kb_qa 分区不挂 execute
        only_read = get_skill_tools(
            user_id=1, project_id=1, allow_execute=False
        )
        self.assertEqual([t.name for t in only_read], ["read_skill_content"])

    def test_skill_catalog_and_unregistered_write(self):
        from orchestrator_integration.skill_catalog import (
            catalog_reject_unregistered_write,
            load_skill_catalog,
            skills_for_zone,
        )
        from orchestrator_integration.skill_zones import ZONE_WEB, zone_skills

        catalog = load_skill_catalog()
        self.assertIn("loca-stude", catalog.skills)
        self.assertEqual(zone_skills(ZONE_WEB), skills_for_zone(ZONE_WEB))
        self.assertIn("url-reader", zone_skills(ZONE_WEB))

        msg = catalog_reject_unregistered_write(
            "loca-stude",
            "python loca_stude_tools.py --action wipe_all_cases",
        )
        self.assertIsNotNone(msg)
        self.assertIn("未在 Skill Catalog 登记", msg)

        ok = catalog_reject_unregistered_write(
            "loca-stude",
            "python loca_stude_tools.py --action get_projects",
        )
        self.assertIsNone(ok)

    def test_playbooks_resolve_for_hot_paths(self):
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import (
            PLAYBOOK_PLATFORM_WRITE,
            PLAYBOOK_TESTCASE_EXECUTE,
            PLAYBOOK_TICKET_KB,
            PLAYBOOK_URL_READ,
            playbook_hint,
            resolve_playbook,
        )
        from orchestrator_integration.skill_zones import resolve_skill_zone

        cases = (
            ("把钉钉工单沉淀到知识库", PLAYBOOK_TICKET_KB),
            ("确认执行测试用例 35", PLAYBOOK_TESTCASE_EXECUTE),
            ("读这个网页 https://example.com", PLAYBOOK_URL_READ),
            ("确认写入平台用例", PLAYBOOK_PLATFORM_WRITE),
        )
        for text, expected in cases:
            d = classify_user_intent(text)
            p = decide_kb_access_policy(text, d)
            z = resolve_skill_zone(text, d, p)
            pb = resolve_playbook(text, z, d, p)
            self.assertIsNotNone(pb, text)
            self.assertEqual(pb.playbook_id, expected, text)
            self.assertIn("本轮剧本", playbook_hint(pb))

    def test_teambition_ticket_not_url_reader(self):
        from orchestrator_integration.intent_router import (
            INTENT_WEB,
            apply_intent_routing,
            classify_user_intent,
            decide_kb_access_policy,
            is_ticket_workorder_intent,
        )
        from orchestrator_integration.playbooks import resolve_playbook
        from orchestrator_integration.skill_catalog import reload_skill_catalog
        from orchestrator_integration.skill_zones import (
            ZONE_TICKET_KB,
            resolve_skill_zone,
        )

        reload_skill_catalog()
        text = (
            "https://www.teambition.com/task/6ad57ab55690775793262052"
            "查看下这个钉钉工单"
        )
        self.assertTrue(is_ticket_workorder_intent(text))
        d = classify_user_intent(text)
        self.assertNotIn(INTENT_WEB, d.intents)
        policy = decide_kb_access_policy(text, d)
        self.assertEqual(policy.reason, "ticket_workorder")
        self.assertTrue(policy.skip_prefetch)
        self.assertFalse(policy.allow_knowledge_search)
        zone = resolve_skill_zone(text, d, policy)
        self.assertEqual(zone.zone_id, ZONE_TICKET_KB)
        self.assertIn("playwright-skill", zone.allowed_skills)
        self.assertIn("workorder-knowledge", zone.allowed_skills)
        pb = resolve_playbook(text, zone, d, policy)
        self.assertIsNotNone(pb)
        self.assertIn("url-reader", pb.skip)

        class _T:
            def __init__(self, name):
                self.name = name

        tools = [
            _T("knowledge_search"),
            _T("read_skill_content"),
            _T("execute_skill_script"),
        ]
        _, kept, hint, _ = apply_intent_routing(
            text, tools, kb_active=True, skill_zone=zone
        )
        self.assertNotIn("knowledge_search", [t.name for t in kept])
        self.assertIn("execute_skill_script", [t.name for t in kept])
        self.assertIn("ticket_kb", hint)
        from orchestrator_integration.playbooks import playbook_hint

        self.assertIn("禁止 url-reader", playbook_hint(pb))
        self.assertNotIn(INTENT_WEB, d.intents)

    def test_tool_telemetry_aggregate(self):
        from orchestrator_integration.tool_telemetry import (
            aggregate_telemetry,
            record_tool_telemetry,
        )

        record_tool_telemetry(
            zone="execute",
            playbook="testcase_execute",
            tool_name="execute_skill_script",
            skill_name="workorder-knowledge",
            success=False,
            rejected_by_allowlist=True,
            error_kind="allowlist",
            duration_ms=12.5,
        )
        record_tool_telemetry(
            zone="execute",
            playbook="testcase_execute",
            tool_name="execute_skill_script",
            skill_name="ui-automation",
            success=True,
            duration_ms=800.0,
        )
        summary = aggregate_telemetry(limit=100)
        self.assertGreaterEqual(summary["total"], 2)
        self.assertIn("execute", summary["zone_hits"])
        self.assertTrue(summary["top_allowlist_rejects"])

    def test_domain_specialist_force_env(self):
        import os
        from orchestrator_integration.domain_specialists import (
            run_specialist_prep,
            should_use_specialist,
        )
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.skill_zones import resolve_skill_zone

        text = "查看已有用例"
        d = classify_user_intent(text)
        z = resolve_skill_zone(text, d)
        old = os.environ.get("FORCE_DOMAIN_SPECIALIST")
        try:
            os.environ["FORCE_DOMAIN_SPECIALIST"] = "1"
            self.assertTrue(should_use_specialist(z))
            spec, hint, allow = run_specialist_prep(z, text)
            self.assertTrue(spec.active)
            self.assertIn("域专家子环", hint)
            self.assertIsNotNone(allow)
            self.assertIn("loca-stude", allow)
        finally:
            if old is None:
                os.environ.pop("FORCE_DOMAIN_SPECIALIST", None)
            else:
                os.environ["FORCE_DOMAIN_SPECIALIST"] = old


class BehaviorMemoryDBTests(TestCase):
    def test_record_creates_db_row_when_vector_upsert_fails(self):
        User = get_user_model()
        user = User.objects.create_user(username="beh1", password="x")
        from orchestrator_integration.behavior_memory import UserBehaviorStore
        from orchestrator_integration.models import UserBehaviorEvent

        store = UserBehaviorStore()
        with patch(
            "orchestrator_integration.behavior_memory._get_embeddings",
            side_effect=RuntimeError("no emb"),
        ):
            store.record(user.id, "tool_call", "工具调用 tool=knowledge_search result=ok")

        self.assertEqual(UserBehaviorEvent.objects.filter(user=user).count(), 1)
        ev = UserBehaviorEvent.objects.get(user=user)
        self.assertEqual(ev.event_type, "tool_call")
        self.assertIn("knowledge_search", ev.summary_text)
