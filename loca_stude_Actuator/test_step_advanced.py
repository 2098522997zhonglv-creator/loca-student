# -*- coding: utf-8 -*-
"""P1：自定义变量 / 条件 / 参数化 / 未支持类型预检。"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any, List, Optional

from data_processor import DataProcessor
from step_advanced import (
    apply_custom_variables,
    apply_front_custom,
    evaluate_condition,
    parse_parametrize_rows,
    preflight_case_config,
    preflight_unsupported_steps,
)


@dataclass
class _Step:
    step_id: int = 1
    step_type: int = 0


@dataclass
class _Page:
    steps: List[Any] = field(default_factory=list)


@dataclass
class _Cfg:
    page_steps: List[Any] = field(default_factory=list)


class StepAdvancedTests(unittest.TestCase):
    def setUp(self):
        self.dp = DataProcessor()

    def test_preflight_rejects_python_type(self):
        err = preflight_unsupported_steps([_Step(1, 5), _Step(2, 0)])
        self.assertIsNotNone(err)
        self.assertIn("Python", err)
        self.assertIn("step_id=1", err)

    def test_preflight_case_ok_for_supported(self):
        cfg = _Cfg(page_steps=[_Page(steps=[_Step(1, 3), _Step(2, 4)])])
        self.assertIsNone(preflight_case_config(cfg))

    def test_apply_custom_variables(self):
        ok, msg = apply_custom_variables(self.dp, {"user": "alice", "n": "1"})
        self.assertTrue(ok)
        self.assertEqual(self.dp.get_cache("user"), "alice")
        self.assertIn("user", msg)

    def test_apply_front_custom_list(self):
        ok, _ = apply_front_custom(
            self.dp, [{"key": "token", "value": "t-1"}, {"key": "x", "value": "y"}]
        )
        self.assertTrue(ok)
        self.assertEqual(self.dp.get_cache("token"), "t-1")

    def test_condition_left_op_right(self):
        self.dp.set_cache("flag", "1")
        ok, msg = evaluate_condition(
            self.dp, {"left": "${{flag}}", "op": "eq", "right": "1"}
        )
        self.assertTrue(ok)
        self.assertIn("True", msg)

        ok2, _ = evaluate_condition(
            self.dp, {"left": "${{flag}}", "op": "eq", "right": "0"}
        )
        self.assertFalse(ok2)

    def test_condition_expression(self):
        self.dp.set_cache("a", "2")
        ok, _ = evaluate_condition(self.dp, {"expression": "${{a}} == 2"})
        self.assertTrue(ok)

    def test_parametrize_rows_isolation_shape(self):
        rows = parse_parametrize_rows([{"u": "a"}, {"u": "b"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["u"], "a")
        # 空 → 单次空行
        self.assertEqual(parse_parametrize_rows(None), [{}])
        self.assertEqual(parse_parametrize_rows({"rows": [{"x": 1}]}), [{"x": 1}])

    def test_data_processor_fork_isolates_runtime_vars(self):
        base = DataProcessor()
        base.set_cache("shared", "base")
        row1 = base.fork()
        row1.set_cache("extracted_token", "t1")
        row2 = base.fork()
        row2.set_cache("user", "beta")
        self.assertEqual(row2.get_cache("shared"), "base")
        self.assertIsNone(row2.get_cache("extracted_token"))
        self.assertEqual(row1.get_cache("extracted_token"), "t1")


if __name__ == "__main__":
    unittest.main()
