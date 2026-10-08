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
            decision, kept, _ = apply_intent_routing(text, tools)
            self.assertIn(intent, decision.intents, text)
            self.assertIn("execute_skill_script", [t.name for t in kept], text)

        decision, kept, _ = apply_intent_routing("先生成一条给我看看", tools)
        self.assertNotIn("execute_skill_script", [t.name for t in kept])

        # 未挂知识库时，纯问答也不裁执行工具
        _, kept, hint = apply_intent_routing("帮我优化一下", tools, kb_active=False)
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
