"""D01 / RC07：审批 pending 解包与服务端一次执行。"""
from django.test import SimpleTestCase


class ApprovalPendingUnpackTests(SimpleTestCase):
    def test_parse_unwraps_toolresult_envelope(self):
        from orchestrator_integration.tool_result import classify_skill_output
        from orchestrator_integration.write_confirmation import parse_needs_confirmation

        raw = (
            '{"status":"needs_confirmation","mode":"confirm","message":"需确认",'
            '"pending":{"skill_name":"loca-stude","command":"python x.py --action add_testcase"}}'
        )
        wrapped = classify_skill_output(text=raw).to_tool_text()
        parsed = parse_needs_confirmation(wrapped)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["status"], "needs_confirmation")
        self.assertEqual(parsed["pending"]["skill_name"], "loca-stude")
        self.assertIn("add_testcase", parsed["pending"]["command"])

    def test_normalize_pending_commands(self):
        from orchestrator_integration.write_confirmation import (
            normalize_pending_commands,
        )

        single = normalize_pending_commands(
            {"skill_name": "loca-stude", "command": "python a.py --action add"}
        )
        self.assertEqual(len(single), 1)
        batch = normalize_pending_commands(
            {
                "commands": [
                    {"skill_name": "loca-stude", "command": "python a.py --action add"},
                    {"skill_name": "loca-stude", "command": ""},
                ]
            }
        )
        self.assertEqual(len(batch), 1)

    def test_resume_message_after_server_execute(self):
        from orchestrator_integration.write_confirmation import build_resume_user_message

        msg = build_resume_user_message(
            "approve",
            {"pending": {"skill_name": "loca-stude", "command": "add"}},
            server_executed={
                "ok": True,
                "results": [{"output": '{"status":"succeeded","data":{"id":1}}'}],
            },
        )
        self.assertIn("服务端已精确执行", msg)
        self.assertNotIn("user_confirmed=true", msg)
