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

    def test_execution_policy_fork_join_and_attribution(self):
        from orchestrator_integration.execution_policy import (
            classify_attribution,
            evaluate_post_condition,
            plan_batch_execution,
        )
        from orchestrator_integration.task_commission import CHANNEL_LIGHT

        err, plan = plan_batch_execution(
            [
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action delete_testcase --case_id 1",
                },
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action get_projects",
                },
            ],
            want_parallel=True,
            channel=CHANNEL_LIGHT,
        )
        # 轻量通道禁 irreversible
        self.assertIsNotNone(err)

        err2, plan2 = plan_batch_execution(
            [
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action get_modules --project_id 1",
                },
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action get_projects",
                },
            ],
            want_parallel=True,
            channel=CHANNEL_LIGHT,
        )
        self.assertIsNone(err2)
        self.assertTrue(plan2.parallel)

        err3, plan3 = plan_batch_execution(
            [
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action add_testcase --name a",
                },
                {
                    "skill_name": "loca-stude",
                    "command": "python x.py --action get_projects",
                },
            ],
            want_parallel=True,
            channel="heavy",
        )
        self.assertIsNone(err3)
        self.assertFalse(plan3.parallel)
        # FR02.1：保留输入顺序（创建在前、查询在后），不做 read/write 全局重排
        self.assertIn("add_testcase", plan3.ordered_commands[0]["command"])
        self.assertIn("get_projects", plan3.ordered_commands[1]["command"])

        self.assertEqual(
            classify_attribution(error_kind="allowlist"), "planning"
        )
        self.assertEqual(
            classify_attribution(result_text="余额不足无法下单"), "business"
        )
        self.assertEqual(
            classify_attribution(result_text="HTTP 500 traceback"), "tool"
        )

        verdict = evaluate_post_condition(
            post_condition="x",
            tool_results=["ok"],
            had_unconfirmed_write=True,
            playbook_id="testcase_execute",
        )
        self.assertFalse(verdict["ok"])

        from orchestrator_integration.task_commission import TaskCommission

        c = TaskCommission(
            task_id="t1",
            zone_id="execute",
            playbook_id="testcase_execute",
            primary_skills=["loca-stude"],
            auxiliary_skills=[],
            short_circuit=True,
            draft_writes=False,
            channel=CHANNEL_LIGHT,
            max_tool_steps=2,
        )
        ok1, _ = c.consume_tool_step(1)
        ok2, _ = c.consume_tool_step(1)
        ok3, detail = c.consume_tool_step(1)
        self.assertTrue(ok1 and ok2)
        self.assertFalse(ok3)
        self.assertIn("预算已用尽", detail)

    def test_task_commission_primary_aux_and_escape_hatch(self):
        from orchestrator_integration.builtin_tools.meta_tools import get_meta_tools
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import resolve_playbook
        from orchestrator_integration.skill_zones import resolve_skill_zone
        from orchestrator_integration.task_commission import (
            commission_task,
        )
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        text = "确认执行测试用例 35"
        d = classify_user_intent(text)
        p = decide_kb_access_policy(text, d)
        z = resolve_skill_zone(text, d, p)
        pb = resolve_playbook(text, z, d, p)
        c = commission_task(
            message=text, zone=z, playbook=pb, decision=d, kb_policy=p
        )
        self.assertTrue(c.short_circuit)
        self.assertEqual(c.channel, "light")
        self.assertIn("ui-automation", c.primary_skills)
        self.assertTrue(c.can_escape())
        ok, _ = c.grant_auxiliary("api-automation")
        self.assertTrue(ok)
        self.assertFalse(c.can_escape())
        ok2, msg2 = c.grant_auxiliary("workorder-knowledge")
        self.assertFalse(ok2)

        set_telemetry_context(
            TelemetryContext(zone=z.zone_id, commission=c, playbook=pb.playbook_id)
        )
        try:
            tools = {t.name: t for t in get_meta_tools()}
            denied = tools["request_tool_expansion"].invoke(
                {"skill_name": "workorder-knowledge", "reason": "test"}
            )
            self.assertIn("denied", denied)

            blocked = tools["report_task_blocked"].invoke(
                {"summary": "缺权限", "missing_capability": "refund"}
            )
            self.assertIn("degraded", blocked)

            self.assertIn("assert_task_postcondition", tools)
            c.last_tool_results.append("execute_testcase ok")
            verdict = tools["assert_task_postcondition"].invoke(
                {"had_unconfirmed_write": False}
            )
            self.assertIn('"ok": true', verdict)  # json.dumps 默认空格
            # 有未确认写入时断言应失败
            bad = tools["assert_task_postcondition"].invoke(
                {"had_unconfirmed_write": True}
            )
            self.assertIn('"ok": false', bad)
        finally:
            set_telemetry_context(None)

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
        self.assertIn("确认写入", payload)
        self.assertIn("executed_readonly", payload)
        self.assertIn("execute_testcase", payload)

    def test_write_confirm_interrupt_roundtrip(self):
        from orchestrator_integration.write_confirmation import (
            build_resume_user_message,
            build_write_confirm_interrupt,
            get_write_confirm_store,
            is_write_confirm_interrupt,
            parse_needs_confirmation,
        )
        from orchestrator_integration.builtin_tools.skill_tools import (
            _needs_write_confirmation_payload,
            detect_user_write_approval,
        )
        from orchestrator_integration.agent_loop_view import _tool_result_sse_events

        raw = _needs_write_confirmation_payload(
            skill_name="loca-stude",
            command="python loca_stude_tools.py --action add_testcase --name demo",
        )
        parsed = parse_needs_confirmation(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["status"], "needs_confirmation")

        events, emitted = _tool_result_sse_events(
            tool_name="execute_skill_script",
            content=raw,
            step_count=1,
            session_id="sess-wc",
            thread_id="1_2_sess-wc",
        )
        self.assertTrue(emitted)
        self.assertEqual(events[0]["type"], "tool_result")
        interrupt = events[1]
        self.assertEqual(interrupt["type"], "interrupt")
        self.assertEqual(interrupt["kind"], "write_confirmation")
        self.assertTrue(is_write_confirm_interrupt(interrupt["interrupt_id"]))
        self.assertEqual(interrupt["action_requests"][0]["name"], "确认写入")

        pending = get_write_confirm_store().get(interrupt["interrupt_id"])
        self.assertIsNotNone(pending)
        approve_msg = build_resume_user_message("approve", pending)
        self.assertTrue(detect_user_write_approval(approve_msg))
        self.assertIn("pending_json", approve_msg)
        self.assertIn("重挂", approve_msg)
        self.assertIn("放行工具", approve_msg)
        # D01：ToolResult 包装后仍能解出 pending
        from orchestrator_integration.tool_result import classify_skill_output

        wrapped = classify_skill_output(text=raw).to_tool_text()
        parsed_wrapped = parse_needs_confirmation(wrapped)
        self.assertIsNotNone(parsed_wrapped)
        self.assertTrue(
            isinstance(parsed_wrapped.get("pending"), dict)
            and bool(parsed_wrapped.get("pending"))
        )
        # 服务端已执行路径：汇总，不再要求模型自批写入
        done_msg = build_resume_user_message(
            "approve",
            pending,
            server_executed={"ok": True, "results": [{"output": "testcase_id=1"}]},
        )
        self.assertIn("服务端已精确执行", done_msg)
        self.assertIn("禁止再次调用写入", done_msg)
        reject_msg = build_resume_user_message("reject", pending)
        self.assertFalse(detect_user_write_approval(reject_msg))

        # build_write_confirm_interrupt 独立路径
        again = build_write_confirm_interrupt(
            session_id="sess-wc",
            thread_id="1_2_sess-wc",
            tool_name="execute_skill_script",
            confirmation=parsed,
        )
        self.assertTrue(is_write_confirm_interrupt(again["interrupt_id"]))
        self.assertIsNotNone(get_write_confirm_store().pop(again["interrupt_id"]))
        get_write_confirm_store().pop(interrupt["interrupt_id"])

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

        # 交互 SPA：拦 browser-use 时改推 playwright，禁止推 url-reader
        spa = prefer_url_reader_instead(
            "browser-use",
            "goto https://callie-us-test-ai-model-tip-1105-web.a.callie.cc/ 加购",
        )
        self.assertIsNotNone(spa)
        self.assertIn("playwright-skill", spa)
        self.assertIn("禁止改调 url-reader", spa)

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
        self.assertTrue(
            ("不在本轮分区白名单" in blocked)
            or ("不在本任务主类授权内" in blocked)
            or ("分区禁止" in blocked),
            blocked,
        )

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
        from orchestrator_integration.builtin_tools.skill_tools import (
            prefer_url_reader_instead,
        )
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

        # 跟进：cookie / 钉钉登录仍走 ticket_kb，且 Teambition goto 不被 prefer_url_reader 误拦
        for follow in (
            "登录钉钉账号：159xxxx zhonglvxxxx",
            "更换cookie TEAMBITION_SESSIONID=abc teambition_lang=zh",
            "执行 skill，用浏览器只读采集这个 Teambition 工单",
        ):
            self.assertTrue(is_ticket_workorder_intent(follow), follow)
            zd = classify_user_intent(follow)
            zp = decide_kb_access_policy(follow, zd)
            zz = resolve_skill_zone(follow, zd, zp)
            self.assertEqual(zz.zone_id, ZONE_TICKET_KB, follow)
            self.assertTrue(zz.allow_execute, follow)

        self.assertIsNone(
            prefer_url_reader_instead(
                "playwright-skill",
                'node run.js "await page.goto(\'https://www.teambition.com/task/abc\'); '
                'const txt=await helpers.getPageText(page);"',
            )
        )
        self.assertIsNotNone(
            prefer_url_reader_instead(
                "playwright-skill",
                'node run.js "await page.goto(\'https://example.com/report.html\');"',
            )
        )

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

    def test_interactive_site_routes_browser_not_url_reader(self):
        from orchestrator_integration.intent_router import (
            INTENT_BROWSER,
            INTENT_WEB,
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import (
            PLAYBOOK_BROWSER_ACT,
            PLAYBOOK_URL_READ,
            resolve_playbook,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_BROWSER,
            ZONE_KB_QA,
            ZONE_WEB,
            resolve_skill_zone,
        )
        from orchestrator_integration.task_commission import commission_task

        # URL + 加购：必须 browser，禁止 url_read
        text = (
            "打开 https://callie-us-test-ai-model-tip-1105-web.a.callie.cc/ ，"
            "找一个已启用的所见所得商品，详情页传图后加入购物车，把购物车里返回的订单号给我，不要支付"
        )
        d = classify_user_intent(text)
        self.assertIn(INTENT_BROWSER, d.intents)
        self.assertNotIn(INTENT_WEB, d.intents)
        p = decide_kb_access_policy(text, d)
        z = resolve_skill_zone(text, d, p)
        self.assertEqual(z.zone_id, ZONE_BROWSER)
        pb = resolve_playbook(text, z, d, p)
        self.assertIsNotNone(pb)
        self.assertEqual(pb.playbook_id, PLAYBOOK_BROWSER_ACT)
        self.assertNotIn("knowledge_search", pb.skip)
        c = commission_task(
            message=text, zone=z, playbook=pb, decision=d, kb_policy=p
        )
        self.assertTrue(c.short_circuit)
        self.assertEqual(c.primary_skills, frozenset({"playwright-skill"}))
        self.assertEqual(c.effective_allowlist(), {"playwright-skill"})
        self.assertGreaterEqual(c.max_tool_steps, 28)
        from orchestrator_integration.task_commission import commission_hint

        hint = commission_hint(c)
        self.assertIn("knowledge_search", hint)
        self.assertNotIn("不要 knowledge_search 绕路", hint)

        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            stage_mount_policy,
        )

        stages = build_stage_plan(text, d, playbook_id=PLAYBOOK_BROWSER_ACT)
        ids = [s.stage_id for s in stages]
        self.assertTrue(
            "browser_interact" in ids or "kb_research" in ids,
            ids,
        )
        bi = next(
            (s for s in stages if s.stage_id in ("browser_interact", "kb_research")),
            stages[0],
        )
        policy = stage_mount_policy(stages, bi.stage_id)
        self.assertIn("knowledge_search", policy["builtin_tools"])

        # 纯问答含「购物车」：仍走 kb_qa，不升 browser
        qa = "购物车逻辑是什么"
        dq = classify_user_intent(qa)
        self.assertNotIn(INTENT_BROWSER, dq.intents)
        zq = resolve_skill_zone(qa, dq, decide_kb_access_policy(qa, dq))
        self.assertEqual(zq.zone_id, ZONE_KB_QA)

        # 静态读网页仍走 url_read
        web = "读这个网页 https://example.com/docs"
        dw = classify_user_intent(web)
        self.assertIn(INTENT_WEB, dw.intents)
        zw = resolve_skill_zone(web, dw, decide_kb_access_policy(web, dw))
        self.assertEqual(zw.zone_id, ZONE_WEB)
        pbw = resolve_playbook(web, zw, dw, decide_kb_access_policy(web, dw))
        self.assertEqual(pbw.playbook_id, PLAYBOOK_URL_READ)

        # 「帮我下单所见即所得」无 URL 也应 browser
        order = (
            "帮我在 CALLIE 环境里下单一个所见即所得商品的订单，"
            "不需要支付把订单号给我即可"
        )
        do = classify_user_intent(order)
        self.assertIn(INTENT_BROWSER, do.intents)
        zo = resolve_skill_zone(order, do, decide_kb_access_policy(order, do))
        self.assertEqual(zo.zone_id, ZONE_BROWSER)

        # SPA 主机即使关键词不全，只要不是明确静态读，也强制 browser
        spa = "打开 https://callie-us-test-ai-model-tip-1105-web.a.callie.cc/"
        ds = classify_user_intent(spa)
        self.assertIn(INTENT_BROWSER, ds.intents)
        self.assertNotIn(INTENT_WEB, ds.intents)
        zs = resolve_skill_zone(spa, ds, decide_kb_access_policy(spa, ds))
        self.assertEqual(zs.zone_id, ZONE_BROWSER)

    def test_autonomy_no_user_script_prompts_and_session_followup(self):
        from orchestrator_integration.builtin_tools.meta_tools import get_meta_tools
        from orchestrator_integration.intent_router import (
            INTENT_BROWSER,
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import (
            PLAYBOOK_BROWSER_ACT,
            resolve_playbook,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_BROWSER,
            ZONE_KB_QA,
            resolve_skill_zone,
            zone_hint,
        )
        from orchestrator_integration.task_commission import (
            commission_hint,
            commission_task,
        )
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        kb_zone = resolve_skill_zone(
            "购物车逻辑是什么",
            classify_user_intent("购物车逻辑是什么"),
            decide_kb_access_policy(
                "购物车逻辑是什么", classify_user_intent("购物车逻辑是什么")
            ),
        )
        self.assertEqual(kb_zone.zone_id, ZONE_KB_QA)
        zh = zone_hint(kb_zone)
        self.assertNotIn("请明确说", zh)
        self.assertNotIn("用 browser-use 执行", zh)
        self.assertIn("禁止要求用户重写", zh)

        follow = (
            "继续：复用 session_id=callie_wysiwyg_cart 与测试图 wysiwyg_test_photo.png"
        )
        df = classify_user_intent(follow)
        self.assertIn(INTENT_BROWSER, df.intents)
        zf = resolve_skill_zone(follow, df, decide_kb_access_policy(follow, df))
        self.assertEqual(zf.zone_id, ZONE_BROWSER)
        pbf = resolve_playbook(follow, zf, df, decide_kb_access_policy(follow, df))
        self.assertEqual(pbf.playbook_id, PLAYBOOK_BROWSER_ACT)
        cf = commission_task(
            message=follow, zone=zf, playbook=pbf, decision=df, kb_policy=None
        )
        self.assertGreaterEqual(cf.max_tool_steps, 28)
        self.assertIn("cart", cf.post_condition.lower())
        ch = commission_hint(cf)
        self.assertIn("禁止要求用户点名 Skill", ch)
        self.assertNotIn("请明确说", ch)

        set_telemetry_context(TelemetryContext(zone=zf.zone_id, commission=cf))
        try:
            blocked = {
                t.name: t for t in get_meta_tools()
            }["report_task_blocked"].invoke(
                {"summary": "filechooser timeout", "missing_capability": "upload"}
            )
            self.assertIn("禁止要求用户重写指令", blocked)
            self.assertNotIn("请明确说", blocked)
            self.assertNotIn("browser-use 执行", blocked)
        finally:
            set_telemetry_context(None)

        ok, budget_msg = cf.consume_tool_step(cf.max_tool_steps + 1)
        self.assertFalse(ok)
        self.assertIn("禁止要求用户重写指令", budget_msg)
        self.assertNotIn("请调用 report_task_blocked 降级，禁止继续重试", budget_msg)

    def test_escape_hatch_grant_syncs_skill_allowlist(self):
        from orchestrator_integration.builtin_tools.meta_tools import get_meta_tools
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools
        from orchestrator_integration.intent_router import (
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.playbooks import resolve_playbook
        from orchestrator_integration.skill_zones import resolve_skill_zone
        from orchestrator_integration.task_commission import commission_task
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        text = "执行 skill"
        d = classify_user_intent(text)
        p = decide_kb_access_policy(text, d)
        z = resolve_skill_zone(text, d, p)
        pb = resolve_playbook(text, z, d, p)
        c = commission_task(
            message=text, zone=z, playbook=pb, decision=d, kb_policy=p
        )
        # 工具创建时白名单仅主类；随后 Escape Hatch 扩权必须对 read 生效
        allow = c.effective_allowlist() or set()
        tools = {
            t.name: t
            for t in get_skill_tools(
                user_id=1,
                project_id=1,
                allowed_skill_names=allow,
                allow_execute=True,
            )
        }
        set_telemetry_context(
            TelemetryContext(
                zone=z.zone_id,
                commission=c,
                playbook=pb.playbook_id if pb else "",
            )
        )
        try:
            denied = tools["read_skill_content"].invoke(
                {"skill_name": "ui-automation"}
            )
            self.assertIn("辅类", denied)

            meta = {t.name: t for t in get_meta_tools()}
            granted = meta["request_tool_expansion"].invoke(
                {
                    "skill_name": "ui-automation",
                    "reason": "需要浏览器自动化完成加购",
                }
            )
            self.assertIn("granted", granted)
            self.assertIn("ui-automation", c.effective_allowlist() or set())

            ok = tools["read_skill_content"].invoke({"skill_name": "ui-automation"})
            self.assertNotIn("不在本轮分区白名单", ok)
            self.assertNotIn("辅类，尚未授权", ok)
            self.assertNotIn("不在本任务主类授权内", ok)
        finally:
            set_telemetry_context(None)

    def test_tool_result_nonzero_exit_and_timeout_unknown(self):
        from orchestrator_integration.tool_result import (
            classify_skill_output,
            summarize_batch_statuses,
        )

        failed = classify_skill_output(
            text="命令执行失败 (退出码: 1)\nboom",
            exit_code=1,
            skill_name="playwright-skill",
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "nonzero_exit")
        self.assertEqual(failed.exit_code, 1)

        unknown = classify_skill_output(
            text="错误: 命令执行超时（120秒）",
            skill_name="playwright-skill",
        )
        self.assertEqual(unknown.status, "unknown")
        self.assertEqual(unknown.error_code, "timeout_unknown")
        self.assertTrue(unknown.retryable)

        summary = summarize_batch_statuses([failed, unknown])
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertEqual(summary["counts"]["unknown"], 1)

    def test_browser_acceptance_rejects_timeout_only(self):
        from orchestrator_integration.execution_policy import (
            evaluate_browser_checkpoints,
            evaluate_post_condition,
        )

        timeout_text = (
            'page.waitForEvent: Timeout 15000ms exceeded while waiting for event "filechooser"'
        )
        chk = evaluate_browser_checkpoints(timeout_text)
        self.assertFalse(chk["ok"])

        verdict = evaluate_post_condition(
            post_condition="浏览器交互已完成",
            tool_results=[timeout_text],
            playbook_id="browser_act",
        )
        self.assertFalse(verdict["ok"])

        # 仅 1/1 无预览 → 不通过
        count_only = evaluate_post_condition(
            post_condition="x",
            tool_results=["上传计数 0/1 → 1/1"],
            playbook_id="browser_act",
        )
        self.assertFalse(count_only["ok"])

        # 有加购证据可通过超时检查点
        ok_v = evaluate_post_condition(
            post_condition="x",
            tool_results=["Add To Cart ok; cart_id=abc123; preview refreshed"],
            playbook_id="browser_act",
        )
        self.assertTrue(ok_v["ok"])

    def test_route_correct_keeps_browser_without_execute_keyword(self):
        from orchestrator_integration.intent_router import (
            INTENT_BROWSER,
            IntentDecision,
            classify_user_intent,
            decide_kb_access_policy,
        )
        from orchestrator_integration.skill_zones import (
            ZONE_BROWSER,
            ZONE_KB_QA,
            SkillZone,
            correct_misrouted_zone,
            resolve_skill_zone,
            zone_skills,
        )

        # 自然表达：无需「执行」
        text = (
            "帮我上传后加购 https://callie-us-test-ai-model-tip-1105-web.a.callie.cc/x "
            "不要支付"
        )
        d = classify_user_intent(text)
        self.assertIn(INTENT_BROWSER, d.intents)
        z = resolve_skill_zone(text, d, decide_kb_access_policy(text, d))
        self.assertEqual(z.zone_id, ZONE_BROWSER)

        # 误判 kb_qa + SPA → 有界纠正到 browser
        bad = SkillZone(ZONE_KB_QA, frozenset(), allow_execute=False, reason="kb_qa")
        fixed = correct_misrouted_zone(
            text,
            bad,
            IntentDecision(intents=set(), primary="qa"),
        )
        self.assertEqual(fixed.zone_id, ZONE_BROWSER)
        self.assertTrue(fixed.reason.startswith("route_correct"))
        self.assertEqual(fixed.allowed_skills, zone_skills(ZONE_BROWSER))

    def test_stage_plan_and_auth_scope(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            infer_auth_scope,
            is_composite_request,
        )

        text = "根据知识库登录规则生成用例并保存后执行，再汇总"
        d = classify_user_intent(text)
        self.assertTrue(is_composite_request(text, d))
        stages = build_stage_plan(text, d)
        ids = [s.stage_id for s in stages]
        self.assertIn("kb_research", ids)
        self.assertIn("persist", ids)
        self.assertIn("execute", ids)
        self.assertEqual(ids[-1], "summarize")

        auth = infer_auth_scope(
            "详情页传图后加入购物车，把购物车标识给我，不要支付"
        )
        self.assertIn("add_to_cart", auth["allow_actions"])
        self.assertIn("upload", auth["allow_actions"])
        self.assertIn("pay", auth["deny_actions"])
        self.assertIn("submit_order", auth["deny_actions"])

    def test_knowledge_claim_and_product_consistency(self):
        from orchestrator_integration.knowledge_claims import (
            annotate_prefetch_chunk,
            assert_target_product_consistency,
            classify_kb_claim,
        )

        claim = classify_kb_claim(
            text="文档写明目前仅有 143 和 dnd 展示支持，当前分支不同因此不支持",
            source_doc="商品中心操作要点",
        )
        self.assertEqual(claim["claim_type"], "inference")
        self.assertTrue(claim["warnings"])

        bad = assert_target_product_consistency(
            target_url="https://a.callie.cc/p1",
            other_product_note="另一商品属性区为空",
        )
        self.assertFalse(bad["ok"])

        annotated = annotate_prefetch_chunk(
            content="示例订单号 12345678901234567 仅供参考",
            source="操作手册",
            score=0.88,
            index=1,
        )
        self.assertIn("claim_type=", annotated)
        self.assertIn("不得当作本次执行结果", annotated)

    def test_retry_policy_and_dependent_skips(self):
        from orchestrator_integration.execution_policy import (
            evaluate_delivery_evidence,
            evaluate_post_condition,
            resolve_dependent_skips,
        )
        from orchestrator_integration.reconcile import build_reconcile_plan
        from orchestrator_integration.retry_policy import decide_retry
        from orchestrator_integration.skill_catalog import reload_skill_catalog

        reload_skill_catalog()
        heal = decide_retry(
            skill_name="playwright-skill",
            attempt=0,
            channel="light",
            error_text="waiting for event 'filechooser' timeout",
        )
        self.assertTrue(heal.allow)
        self.assertEqual(heal.next_action, "heal")

        once = decide_retry(
            skill_name="ui-automation",
            attempt=0,
            channel="light",
            tool_retryable=True,
            error_text="HTTP 503",
        )
        self.assertTrue(once.allow)
        exhausted = decide_retry(
            skill_name="ui-automation",
            attempt=1,
            channel="light",
            tool_retryable=True,
            error_text="HTTP 503",
        )
        self.assertFalse(exhausted.allow)
        self.assertEqual(exhausted.next_action, "reconcile")

        biz = decide_retry(
            skill_name="loca-stude",
            attempt=0,
            error_text="余额不足无法下单",
        )
        self.assertFalse(biz.allow)

        # AC13：超时必须先核对，禁止重放
        plan = build_reconcile_plan(
            skill_name="ui-automation",
            text="execute_testcase timeout execution_id=e99",
            status="unknown",
        )
        self.assertTrue(plan["must_reconcile"])
        self.assertFalse(plan["allow_replay"])
        self.assertEqual(plan["reconcile_action"], "get_execution_records")
        self.assertEqual(plan["resource_keys"].get("execution_id"), "e99")

        planned = resolve_dependent_skips(
            [
                {"step_id": "a", "status": "failed", "depends_on": [], "on_failure": "stop_dependents"},
                {"step_id": "b", "status": "pending", "depends_on": ["a"], "on_failure": "stop_dependents"},
                {"step_id": "c", "status": "pending", "depends_on": [], "on_failure": "continue"},
            ]
        )
        by_id = {s["step_id"]: s for s in planned}
        self.assertEqual(by_id["b"]["status"], "skipped")
        self.assertEqual(by_id["c"]["status"], "pending")

        # AC28 / AC22 交付门禁
        delivery = evaluate_delivery_evidence(
            tool_results=[
                "示例 order_sn=12345678901234567 交付结果 order_sn=12345678901234567"
            ],
            auth_scope={"allow_actions": ["add_to_cart"], "deny_actions": ["pay", "submit_order"]},
        )
        self.assertFalse(delivery["ok"])
        order_block = evaluate_delivery_evidence(
            tool_results=["提交订单成功 order_sn=999"],
            auth_scope={"allow_actions": ["add_to_cart"], "deny_actions": ["pay", "submit_order"]},
        )
        self.assertFalse(order_block["ok"])

        # AC26/AC29：加购无类型化标识 → 未完全完成
        acc = evaluate_post_condition(
            post_condition="x",
            tool_results=["Add To Cart ok; preview refreshed; 已加入购物车"],
            playbook_id="browser_act",
            auth_scope={"allow_actions": ["add_to_cart"]},
        )
        self.assertFalse(acc["ok"])
        self.assertTrue(any("类型化标识" in r for r in acc["reasons"]))

    def test_stage_mount_and_probe_hint(self):
        from orchestrator_integration.agent_runtime import (
            format_probe_hint,
            probe_browser_resources,
        )
        from orchestrator_integration.idempotency import (
            build_idempotency_key,
            idempotency_env,
            should_inject_idempotency,
        )
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import (
            build_stage_plan,
            format_stage_plan_hint,
            stage_mount_policy,
        )

        text = "根据知识库登录规则生成用例并保存后执行，再汇总"
        d = classify_user_intent(text)
        stages = build_stage_plan(text, d)
        policy = stage_mount_policy(stages, stages[0].stage_id)
        self.assertTrue(policy["restrict"])
        self.assertIn("knowledge_search", policy["builtin_tools"])
        hint = format_stage_plan_hint(stages, stages[0].stage_id)
        self.assertIn("阶段计划", hint)
        self.assertIn(stages[0].stage_id, hint)

        missing = probe_browser_resources(file_path="/tmp/no_such_upload_xyz.png")
        self.assertFalse(missing["probe_result"]["file"]["accessible"])
        ph = format_probe_hint(missing)
        self.assertIn("禁止宣称上传成功", ph)

        sess = probe_browser_resources(session_id="sess-not-exist-xyz")
        self.assertIs(sess["probe_result"]["session"]["alive"], False)
        self.assertFalse(sess["ok_to_reuse"])
        self.assertIn("login_state", sess["probe_result"]["session"])

        # 域名探测字段存在（localhost 结果依赖环境，只断言结构）
        dom = probe_browser_resources(target_url="https://127.0.0.1/")
        self.assertIn("domain", dom["probe_result"])
        self.assertIn("ok", dom["probe_result"]["domain"])

        # Skill 脚本会读取 IDEMPOTENCY_KEY 写入请求头
        import importlib
        import os
        import sys
        from pathlib import Path

        skill_dir = (
            Path(__file__).resolve().parents[2]
            / "bundled_skills"
            / "loca_stude_skills"
        )
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))
        os.environ["IDEMPOTENCY_KEY"] = "unit-test-idem-key"
        try:
            import loca_stude_tools as lst

            importlib.reload(lst)
            hdr = lst._headers()
            self.assertEqual(hdr.get("Idempotency-Key"), "unit-test-idem-key")
        finally:
            os.environ.pop("IDEMPOTENCY_KEY", None)

        self.assertTrue(
            should_inject_idempotency(
                "loca-stude", "python x.py --action add_testcase"
            )
        )
        self.assertFalse(
            should_inject_idempotency(
                "loca-stude", "python x.py --action get_testcases"
            )
        )
        cmd = "python x.py --action add_testcase --name demo"
        key = build_idempotency_key(
            task_id="t1", skill_name="loca-stude", command=cmd
        )
        env = idempotency_env(
            task_id="t1", skill_name="loca-stude", command=cmd
        )
        self.assertEqual(env.get("IDEMPOTENCY_KEY"), key)
        self.assertIn("LOCA_IDEMPOTENCY_NOTE", env)


class BehaviorMemoryDBTests(TestCase):
    def test_approval_consume_once(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import fingerprint_payload
        from orchestrator_integration.models import ApprovalRequest
        from orchestrator_integration.write_confirmation import consume_approval

        User = get_user_model()
        user = User.objects.create_user(username="apr1", password="x")
        chat = ChatSession.objects.create(
            user=user,
            session_id="sess-apr-1",
            title="t",
        )
        args = {"pending": {"command": "x"}}
        ApprovalRequest.objects.create(
            interrupt_id="write_confirm_test1",
            user=user,
            session_id=chat.session_id,
            action_name="execute_skill_script",
            action_args=args,
            param_fingerprint=fingerprint_payload(args.get("pending")),
            status="pending",
        )
        ok1 = consume_approval(
            "write_confirm_test1", user_id=user.id, decision="approve"
        )
        self.assertTrue(ok1["ok"])
        ok2 = consume_approval(
            "write_confirm_test1", user_id=user.id, decision="approve"
        )
        self.assertFalse(ok2["ok"])
        self.assertEqual(ok2["error_code"], "approval_consumed")

    def test_runtime_lease_and_budget(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            acquire_lease,
            assert_task_access,
            consume_budget,
            create_or_resume_task,
            finalize_cancel,
            heartbeat_lease,
            request_cancel,
            resume_preflight,
            task_snapshot,
            try_claim_execution,
        )
        from orchestrator_integration.models import AgentStep
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="rt1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-rt-1", title="t")
        c = TaskCommission(
            task_id="tid123",
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
            max_tool_steps=5,
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="加购不要支付",
            commission=c,
            auth_scope={"allow_actions": ["add_to_cart"]},
            stage_plan=[{"stage_id": "browser_interact", "goal": "x"}],
            lease_owner="w1",
        )
        self.assertTrue(acquire_lease(task, "w1"))
        # AC08：他人无法抢租约
        claim2 = try_claim_execution(task, "w2")
        self.assertFalse(claim2["acquired"])
        self.assertEqual(claim2["error_code"], "lease_held")
        self.assertTrue(heartbeat_lease(task, "w1"))
        self.assertFalse(heartbeat_lease(task, "w2"))
        access = assert_task_access(task, user_id=user.id, session_id="sess-rt-1")
        self.assertTrue(access["ok"])
        bad_access = assert_task_access(task, user_id=user.id + 99)
        self.assertFalse(bad_access["ok"])
        ok, _ = consume_budget(task, tool_steps=2, retries=1, duration_ms=100)
        self.assertTrue(ok)
        self.assertEqual(task.budget.get("used_retries"), 1)
        bad, msg = consume_budget(task, tool_steps=10)
        self.assertFalse(bad)
        self.assertIn("budget_tool_steps_exceeded", msg)
        AgentStep.objects.create(
            task=task,
            step_number=1,
            step_id="op-unknown-1",
            status="unknown",
            tool_name="ui-automation",
            tool_output_summary="execute_testcase timeout waiting for result",
            structured_result={"status": "unknown", "data": {"execution_id": "e1"}},
        )
        pre = resume_preflight(task)
        self.assertTrue(pre["must_reconcile"])
        cancel = request_cancel(task)
        self.assertEqual(cancel["status"], "cancelling")
        # FLOW04：unknown 且带外部 execution_id 时不得宣称已取消
        fin = finalize_cancel(task)
        self.assertEqual(fin["status"], "cancelling")
        self.assertGreaterEqual(fin.get("uncertain_count", 0), 1)
        fin_forced = finalize_cancel(task, force=True)
        self.assertEqual(fin_forced["status"], "cancelled")
        snap = task_snapshot(task)
        self.assertEqual(snap["task_id"], "tid123")
        self.assertIn("events", snap)

    def test_agent_runtime_stage_advance_and_metrics(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            AgentRuntime,
            quality_metrics,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="rt2", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-rt-2", title="t")
        c = TaskCommission(
            task_id="tid-stage",
            zone_id="execute",
            playbook_id="",
            primary_skills=frozenset({"loca-stude"}),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="heavy",
            max_tool_steps=12,
        )
        stages = [
            {
                "stage_id": "kb_research",
                "goal": "查规则",
                "tool_capabilities": ["knowledge_search"],
                "depends_on": [],
                "acceptance": "来源",
                "auth_actions": [],
            },
            {
                "stage_id": "persist",
                "goal": "保存",
                "tool_capabilities": ["loca-stude"],
                "depends_on": ["kb_research"],
                "acceptance": "id",
                "auth_actions": ["write"],
            },
            {
                "stage_id": "summarize",
                "goal": "汇总",
                "tool_capabilities": [],
                "depends_on": ["persist"],
                "acceptance": "汇总",
                "auth_actions": [],
            },
        ]
        rt = AgentRuntime(lease_owner="w-stage")
        task = rt.start(
            session=chat,
            user=user,
            project_id=None,
            goal="根据知识库生成并保存后汇总",
            commission=c,
            stage_plan=stages,
        )
        self.assertEqual(task.current_stage, "kb_research")
        adv = rt.maybe_advance(
            tool_results=["## 知识库预检索结果\n来源: 登录规则文档"]
        )
        self.assertTrue(adv["advanced"])
        self.assertEqual(adv["current_stage"], "persist")
        self.assertEqual(task.plan_version, 2)
        # 非末阶段 accept(ok) 应延迟 completed，且不得二次推进（FLOW02）
        deferred = rt.accept({"ok": True, "reasons": []})
        self.assertTrue(deferred.get("deferred_completion"))
        self.assertEqual(task.current_stage, "persist")
        adv2 = rt.maybe_advance(
            tool_results=["testcase_id=42 创建成功 id=42"]
        )
        self.assertTrue(adv2["advanced"])
        self.assertEqual(task.current_stage, "summarize")
        metrics = quality_metrics(user_id=user.id, limit=50)
        self.assertGreaterEqual(metrics["tasks_sampled"], 1)
        self.assertIn("telemetry", metrics)
        self.assertIn("stage_advance_events", metrics)

    def test_approval_fingerprint_and_cross_user(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import fingerprint_payload
        from orchestrator_integration.models import ApprovalRequest
        from orchestrator_integration.write_confirmation import consume_approval

        User = get_user_model()
        user = User.objects.create_user(username="apr2", password="x")
        other = User.objects.create_user(username="apr3", password="x")
        chat = ChatSession.objects.create(
            user=user, session_id="sess-apr-2", title="t"
        )
        pending = {"skill_name": "loca-stude", "command": "add_testcase"}
        fp = fingerprint_payload(pending)
        ApprovalRequest.objects.create(
            interrupt_id="write_confirm_fp1",
            user=user,
            session_id=chat.session_id,
            action_name="execute_skill_script",
            action_args={"pending": pending},
            param_fingerprint=fp,
            status="pending",
        )
        denied = consume_approval(
            "write_confirm_fp1", user_id=other.id, decision="approve"
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error_code"], "permission_denied")
        mismatch = consume_approval(
            "write_confirm_fp1",
            user_id=user.id,
            decision="approve",
            expected_fingerprint="deadbeef",
        )
        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["error_code"], "param_fingerprint_mismatch")
        ok = consume_approval(
            "write_confirm_fp1",
            user_id=user.id,
            decision="approve",
            expected_fingerprint=fp,
            session_id=chat.session_id,
        )
        self.assertTrue(ok["ok"])
        again = consume_approval(
            "write_confirm_fp1", user_id=user.id, decision="approve"
        )
        self.assertFalse(again["ok"])
        self.assertEqual(again["error_code"], "approval_consumed")

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


class RemainingRequirementGapTests(TestCase):
    """剩余缺口接线回归：Token 预算、步骤依赖、取消传播、核对落库。"""

    def test_session_token_usage_consumes_task_budget(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_loop_view import AgentLoopStreamAPIView
        from orchestrator_integration.agent_runtime import create_or_resume_task
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="tokgate", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-tok-gate", title="t")
        c = TaskCommission(
            task_id="tg1",
            zone_id="general",
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="light",
            max_tokens=50,
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="x",
            commission=c,
            lease_owner="w1",
        )
        AgentLoopStreamAPIView()._update_session_token_usage(
            chat.session_id, 20, 20, user_id=user.id
        )
        task.refresh_from_db()
        self.assertEqual(task.budget.get("used_tokens"), 40)
        # R21：超额仍入账实际消耗，并标记 over_budget；不抛异常
        AgentLoopStreamAPIView()._update_session_token_usage(
            chat.session_id, 20, 0, user_id=user.id
        )
        task.refresh_from_db()
        self.assertEqual(task.budget.get("used_tokens"), 60)
        self.assertTrue(task.budget.get("over_budget"))

    def test_step_deps_ref_and_cycle(self):
        from orchestrator_integration.step_deps import (
            resolve_output_refs,
            validate_batch_deps,
        )

        err = validate_batch_deps(
            [
                {"step_id": "a", "depends_on": ["b"], "command": "x"},
                {"step_id": "b", "depends_on": ["a"], "command": "y"},
            ]
        )
        self.assertIn("环", err or "")
        miss, raw = resolve_output_refs(
            "python x.py --id ${a.case_id}",
            {"a": {"status": "failed", "data": {"case_id": "9"}}},
        )
        self.assertIsNotNone(miss)
        ok, resolved = resolve_output_refs(
            "python x.py --id ${a.case_id}",
            {"a": {"status": "succeeded", "data": {"case_id": "42"}}},
        )
        self.assertIsNone(ok)
        self.assertIn("42", resolved)

    def test_unknown_step_stores_reconcile_plan(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            create_or_resume_task,
            record_step_result,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="rec1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-rec-1", title="t")
        c = TaskCommission(
            task_id="rec",
            zone_id="execute",
            playbook_id="testcase_execute",
            primary_skills=frozenset({"ui-automation"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="执行",
            commission=c,
            lease_owner="w1",
        )
        step = record_step_result(
            task,
            tool_name="ui-automation",
            tool_input={"command": "execute"},
            structured={
                "status": "unknown",
                "operation_id": "op-u1",
                "summary": "超时",
                "data": {"execution_id": "exec-9"},
                "idempotency_key": "idem-abc",
            },
            summary="错误: 超时",
        )
        self.assertEqual(step.status, "unknown")
        self.assertIn("reconcile", step.evidence or {})
        self.assertTrue((step.evidence or {}).get("reconcile", {}).get("must_reconcile"))
        self.assertEqual((step.evidence or {}).get("idempotency_key"), "idem-abc")

    def test_stop_signal_blocks_new_skill_action(self):
        from orchestrator_integration.builtin_tools.skill_tools import (
            SkillExecutionCancelled,
            _run_shell_cancellable,
            _should_stop_execution,
        )
        from orchestrator_integration.stop_signal import (
            clear_stop_signal,
            set_stop_signal,
        )
        from orchestrator_integration.telemetry_context import (
            TelemetryContext,
            set_telemetry_context,
        )

        set_telemetry_context(TelemetryContext(session_id="sess-stop-skill"))
        set_stop_signal("sess-stop-skill")
        try:
            self.assertTrue(_should_stop_execution())
            with self.assertRaises(SkillExecutionCancelled):
                _run_shell_cancellable(
                    "echo hi",
                    cwd="/tmp",
                    env=dict(**__import__("os").environ),
                    timeout=5,
                    session_id="sess-stop-skill",
                )
        finally:
            clear_stop_signal("sess-stop-skill")
            set_telemetry_context(None)


class AuditFlowRegressionTests(TestCase):
    """2026-11-12 复查报告 FLOW/边界回归。"""

    def test_continue_preserves_original_goal_and_authorization(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import create_or_resume_task
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="cont1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-cont-1", title="t")
        c = TaskCommission(
            task_id="c1",
            zone_id="browser",
            playbook_id="browser_act",
            primary_skills=frozenset({"playwright-skill"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=False,
            channel="light",
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="详情页加购，不要下单",
            commission=c,
            auth_scope={
                "allow_actions": ["add_to_cart"],
                "deny_actions": ["submit_order"],
            },
            lease_owner="w1",
        )
        original_goal = task.goal
        cont = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="继续完成剩下的",
            commission=c,
            auth_scope={"allow_actions": ["submit_order"]},
            lease_owner="w1",
        )
        self.assertEqual(cont.id, task.id)
        self.assertEqual(cont.goal, original_goal)
        self.assertIn("add_to_cart", cont.auth_scope.get("allow_actions") or [])
        self.assertIn("submit_order", cont.auth_scope.get("deny_actions") or [])
        self.assertNotIn("submit_order", cont.auth_scope.get("allow_actions") or [])

    def test_token_budget_enforced(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            consume_budget,
            create_or_resume_task,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="tok1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-tok-1", title="t")
        c = TaskCommission(
            task_id="tok",
            zone_id="general",
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="light",
            max_tokens=100,
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="x",
            commission=c,
            lease_owner="w1",
        )
        self.assertEqual(task.budget.get("max_tokens"), 100)
        ok, _ = consume_budget(task, tokens=80)
        self.assertTrue(ok)
        bad, msg = consume_budget(task, tokens=30)
        self.assertFalse(bad)
        self.assertIn("budget_tokens_exceeded", msg)

    def test_failed_cart_and_stale_preview_cannot_pass(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        bad = evaluate_post_condition(
            post_condition="done",
            tool_results=[
                "Timeout uploading; Add To Cart failed; preview not refreshed; cart_id=unknown"
            ],
            playbook_id="browser_act",
            auth_scope={"allow_actions": ["add_to_cart", "upload"]},
        )
        self.assertFalse(bad["ok"])

    def test_negated_order_action_is_not_authorization(self):
        from orchestrator_integration.stage_plan import infer_auth_scope

        auth = infer_auth_scope("加购后把标识给我，不要下单，不要支付")
        self.assertIn("add_to_cart", auth["allow_actions"])
        self.assertIn("submit_order", auth["deny_actions"])
        self.assertNotIn("submit_order", auth["allow_actions"])
        self.assertIn("pay", auth["deny_actions"])

    def test_business_rejection_cannot_be_success(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition
        from orchestrator_integration.tool_result import classify_skill_output

        tr = classify_skill_output(text="余额不足，无法加购", skill_name="playwright-skill")
        self.assertEqual(tr.status, "failed")
        self.assertEqual(tr.error_code, "business_rejected")
        acc = evaluate_post_condition(
            post_condition="x",
            tool_results=["余额不足，业务校验失败"],
            playbook_id="browser_act",
        )
        self.assertFalse(acc["ok"])

    def test_skipped_batch_cannot_be_all_success(self):
        from orchestrator_integration.tool_result import ToolResult, summarize_batch_statuses

        summary = summarize_batch_statuses(
            [
                ToolResult(status="succeeded", operation_id="a"),
                ToolResult(status="skipped", operation_id="b"),
            ]
        )
        self.assertFalse(summary["ok"])
        self.assertIn("跳过", summary["message"])

    def test_identifier_fields_populated_for_observed_cart(self):
        from orchestrator_integration.tool_result import classify_skill_output

        tr = classify_skill_output(
            text="Add To Cart ok; cart_id=cart_9f3a; preview refreshed",
            skill_name="playwright-skill",
        )
        self.assertEqual(tr.resource_type, "cart_id")
        self.assertEqual(tr.resource_id, "cart_9f3a")

    def test_native_dialog_requirement_not_satisfied_by_set_input(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="upload",
            tool_results=["used setInputFiles; 0/1 → 1/1"],
            playbook_id="browser_act",
            auth_scope={"upload_method_requirement": "native_or_filechooser"},
        )
        self.assertFalse(acc["ok"])
        self.assertTrue(any("原生" in r or "set_input" in r or "setInput" in r for r in acc["reasons"]))

    def test_valid_structured_write_not_rejected_by_error_code_field(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="write",
            tool_results=['{"status": "succeeded", "id": 12, "error_code": null}'],
            playbook_id="platform_write",
        )
        self.assertTrue(acc["ok"])

    def test_testcase_timeout_without_record_cannot_pass(self):
        from orchestrator_integration.execution_policy import evaluate_post_condition

        acc = evaluate_post_condition(
            post_condition="exec",
            tool_results=["错误: 命令执行超时（120秒）"],
            playbook_id="testcase_execute",
        )
        self.assertFalse(acc["ok"])

    def test_composite_task_not_collapsed_by_actual_route_playbook(self):
        from orchestrator_integration.intent_router import classify_user_intent
        from orchestrator_integration.stage_plan import build_stage_plan

        text = "根据知识库登录规则生成用例并保存后执行，再汇总"
        d = classify_user_intent(text)
        stages = build_stage_plan(text, d, playbook_id="testcase_execute")
        ids = [s.stage_id for s in stages]
        self.assertGreater(len(ids), 1)
        self.assertNotEqual(ids, ["execute_testcase"])

    def test_idempotency_key_uses_full_command(self):
        from orchestrator_integration.idempotency import build_idempotency_key

        prefix = "python x.py --action add_testcase --payload " + ("A" * 850)
        k1 = build_idempotency_key(
            task_id="t", skill_name="loca-stude", command=prefix + " end=1"
        )
        k2 = build_idempotency_key(
            task_id="t", skill_name="loca-stude", command=prefix + " end=2"
        )
        self.assertNotEqual(k1, k2)

    def test_stage_failure_not_accepted(self):
        from orchestrator_integration.agent_runtime import stage_acceptance_heuristic

        v = stage_acceptance_heuristic(
            "persist",
            ['{"status": "failed", "command": "add_testcase"} permission denied'],
        )
        self.assertFalse(v["ok"])

    def test_approval_lease_transfer(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_runtime import (
            create_or_resume_task,
            park_for_approval,
            try_claim_execution,
        )
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="lease1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-lease-1", title="t")
        c = TaskCommission(
            task_id="lease",
            zone_id="platform",
            playbook_id="platform_write",
            primary_skills=frozenset({"loca-stude"}),
            auxiliary_skills=frozenset(),
            short_circuit=True,
            draft_writes=True,
            channel="heavy",
        )
        task = create_or_resume_task(
            session=chat,
            user=user,
            project_id=None,
            goal="写库",
            commission=c,
            lease_owner="runner-a",
        )
        park_for_approval(task, interrupt_id="write_confirm_x")
        self.assertEqual(task.status, "awaiting_approval")
        self.assertEqual(task.lease_owner, "")
        claim = try_claim_execution(task, "resume-b")
        self.assertTrue(claim["acquired"])
        self.assertEqual(claim["status"], "running")

    def test_finalize_no_double_stage_advance(self):
        from django.contrib.auth import get_user_model
        from langgraph_integration.models import ChatSession
        from orchestrator_integration.agent_loop_finalize import (
            RuntimeSession,
            finalize_task_acceptance,
        )
        from orchestrator_integration.agent_runtime import AgentRuntime
        from orchestrator_integration.task_commission import TaskCommission

        User = get_user_model()
        user = User.objects.create_user(username="stg1", password="x")
        chat = ChatSession.objects.create(user=user, session_id="sess-stg-1", title="t")
        c = TaskCommission(
            task_id="stg",
            zone_id="general",
            playbook_id="",
            primary_skills=frozenset(),
            auxiliary_skills=frozenset(),
            short_circuit=False,
            draft_writes=False,
            channel="light",
        )
        c.last_tool_results = ['预检索完成 "status": "succeeded" 来源=doc1']
        c.post_condition = "done"
        rt = AgentRuntime(lease_owner="w1")
        task = rt.start(
            session=chat,
            user=user,
            project_id=None,
            goal="复合",
            commission=c,
            stage_plan=[
                {"stage_id": "kb_research", "goal": "kb"},
                {"stage_id": "generate_draft", "goal": "draft"},
                {"stage_id": "persist", "goal": "save"},
            ],
        )
        self.assertEqual(task.current_stage, "kb_research")
        session = RuntimeSession(runtime=rt, commission=c)
        finalize_task_acceptance(session)
        task.refresh_from_db()
        self.assertEqual(task.current_stage, "generate_draft")
        self.assertNotEqual(task.status, "completed")


class McpToolResultAdapterTests(SimpleTestCase):
    def test_mcp_list_and_json_adapted_to_tool_result(self):
        from orchestrator_integration.tool_result import adapt_mcp_tool_result

        tr = adapt_mcp_tool_result(
            [{"type": "text", "text": '{"success": false, "code": 400, "message": "bad"}'}],
            tool_name="demo_mcp",
        )
        self.assertEqual(tr.status, "failed")
        self.assertEqual(tr.evidence.get("adapter"), "mcp")
        self.assertIn("demo_mcp", tr.evidence.get("tool_name", ""))

        ok = adapt_mcp_tool_result(
            {"success": True, "data": {"cart_id": "c1"}},
            tool_name="cart_tool",
        )
        self.assertEqual(ok.status, "succeeded")
        self.assertEqual(ok.resource_type, "cart_id")
        self.assertEqual(ok.resource_id, "c1")

        already = adapt_mcp_tool_result(
            {
                "status": "succeeded",
                "operation_id": "op1",
                "data": {"x": 1},
                "summary": "done",
            }
        )
        self.assertEqual(already.operation_id, "op1")
        self.assertEqual(already.data.get("x"), 1)


class RemoteIdempotencyDedupeTests(SimpleTestCase):
    """证明：携带 Idempotency-Key 的远端写入可去重（本地模拟服务端）。"""

    def test_remote_server_dedupes_by_idempotency_key(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        store = {"seen": {}, "calls": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                key = self.headers.get("Idempotency-Key") or self.headers.get(
                    "X-Idempotency-Key"
                )
                store["calls"] += 1
                if key and key in store["seen"]:
                    payload = dict(store["seen"][key])
                    payload["deduped"] = True
                    data = json.dumps(payload).encode()
                    self.send_response(200)
                else:
                    payload = {
                        "success": True,
                        "data": {"id": store["calls"]},
                        "deduped": False,
                    }
                    if key:
                        store["seen"][key] = payload
                    data = json.dumps(payload).encode()
                    self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            import urllib.request

            from orchestrator_integration.idempotency import (
                build_idempotency_key,
                idempotency_env,
            )

            key = build_idempotency_key(
                task_id="t-idem",
                skill_name="loca-stude",
                command="python x.py --action add_testcase --name demo",
            )
            env = idempotency_env(
                task_id="t-idem",
                skill_name="loca-stude",
                command="python x.py --action add_testcase --name demo",
            )
            self.assertEqual(env["IDEMPOTENCY_KEY"], key)

            def _post():
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/write",
                    data=b'{"name":"demo"}',
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": key,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return json.loads(resp.read().decode())

            first = _post()
            second = _post()
            self.assertFalse(first.get("deduped"))
            self.assertTrue(second.get("deduped"))
            self.assertEqual(first["data"]["id"], second["data"]["id"])
            self.assertEqual(store["calls"], 2)
        finally:
            server.shutdown()
