"""Browser-test observations and delivery, stored in the existing AgentTask facts.

A completed test run may contain failed business assertions. A rejected cart
mutation is evidence for that row, not a request to debug another product.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


def contract_for_goal(goal: str):
    if not re.search(r"测试|验收|验证|test", goal or "", re.I) or not re.search(r"加购|购物车|add.to.cart", goal or "", re.I):
        return None
    # Upload/checkout contracts remain with their existing executors. This
    # adapter handles browser cart tests, never infers an unobserved upload.
    if re.search(r"上传|传图|upload photo", goal, re.I):
        return None
    urls = re.findall(r"https?://[^\s，,；;）)]+", goal)
    if not urls:
        return None
    path = re.search(r"/[A-Za-z0-9_/-]+-pdp-\d+", goal)
    target = urljoin(urls[0], path.group(0)) if path else urls[0]
    if urlparse(target).path in {"", "/"}:
        return None  # Product-discovery tasks keep their existing planning path.
    values = list(dict.fromkeys(re.findall(r"(?:文本|text)(?:\s*[:：=]\s*|\s+)[\"'“]?([\w.-]+)", goal, re.I)))
    rows = [{"key": f"row-{i+1}", "value": value} for i, value in enumerate(values)] or [{"key": "row-1", "value": ""}]
    return {
        "target_url": target, "expected_rows": rows, "rows": {}, "sources": [],
        "knowledge_checked": False, "page_checked": False,
        "requires_empty_check": bool(re.search(r"为空|空文本|空值|empty", goal, re.I)),
        "empty_check": None, "cart_readback": None, "last_inputs": [],
        "requires_screenshot": "截图" in goal, "artifacts": [],
    }


def observe(state: dict, *, tool_name: str, result: dict, command: str = "") -> dict:
    state = dict(state)
    data = result.get("data") or {}
    raw = str(data.get("raw_text") or "")
    if tool_name == "knowledge_search" and result.get("status") == "succeeded" and raw:
        state["knowledge_checked"] = True
        sources = re.findall(r"来源[:：]\s*([^\n)）]+)", raw)
        state["sources"] = list(dict.fromkeys(state.get("sources", []) + sources))[:15]
    obs = data.get("browser_observation")
    if not isinstance(obs, dict):
        return state
    page_url = str(obs.get("page_url") or "")
    navigated = any(r.get("action") == "navigation" and 200 <= r.get("http_status", 0) < 300 for r in obs.get("responses", []))
    if page_url.rstrip("/") == state["target_url"].rstrip("/") and navigated and obs.get("ready") in {"interactive", "complete"}:
        state["page_checked"] = True
    state["last_inputs"] = obs.get("input_values") or []
    state["artifacts"] = list({x["url"]: x for x in state.get("artifacts", []) + (data.get("artifacts") or []) if isinstance(x, dict) and x.get("url")}.values())
    if state.get("requires_empty_check") and re.search(r"\.click\(|click\(", command):
        errors = obs.get("empty_validation_errors") or (obs.get("validation_errors") if obs.get("empty_validation_blocked") and not obs.get("cart_writes_started") else [])
        if errors:
            state["empty_check"] = {"passed": True, "errors": errors, "operation_id": result["operation_id"]}
    for response in obs.get("responses") or []:
        if response.get("action") != "cart":
            continue
        if response.get("method") == "GET" and response.get("http_status") == 200 and "cart_count" in response:
            state["cart_readback"] = {**response, "operation_id": result["operation_id"]}
        if response.get("method") not in {"POST", "PUT", "PATCH"} or response.get("success") is None:
            continue
        inputs = response.get("request_values") or state["last_inputs"]
        for row in state["expected_rows"]:
            if row["value"] and row["value"] not in inputs:
                continue
            # Once this row has an explicit non-retryable outcome, later
            # diagnostic calls cannot erase it or silently declare success.
            previous = state["rows"].get(row["key"])
            if previous and not previous.get("retryable"):
                continue
            state["rows"][row["key"]] = {
                **response, "operation_id": result["operation_id"],
                "result": "passed" if response["success"] else "failed",
                "preview": response.get("previews") or [],
            }
    # Some storefronts render the known empty cart without a new API request.
    # Explicitly rejected writes plus an observed empty cart page are valid
    # negative-test evidence. They can never establish a successful cart row.
    if (not state.get("cart_readback") and obs.get("cart_empty") and navigated
            and urlparse(page_url).netloc == urlparse(state["target_url"]).netloc
            and urlparse(page_url).path.rstrip("/") in {"/cart", "/cart/index"}
            and all(state["rows"].get(r["key"], {}).get("result") == "failed"
                    and not state["rows"][r["key"]].get("retryable") for r in state["expected_rows"])):
        state["cart_readback"] = {"http_status": 200, "cart_count": 0, "operation_id": result["operation_id"],
                                  "observed_via": "rendered_cart_page", "page_url": page_url}
    return state


def delivery_ready(state: dict) -> bool:
    passed = [x for x in state.get("rows", {}).values() if x.get("result") == "passed"]
    cart = state.get("cart_readback") or {}
    # A transport success cannot finish a positive cart test with an empty cart
    # or without an observed business identifier.
    if passed and (cart.get("cart_count", 0) < len(passed) or not any(
        (x.get("identifiers") or {}).get("cart_id") or (x.get("identifiers") or {}).get("cart_item_id")
        for x in passed + [cart]
    )):
        return False
    # A positive result needs row-level cart evidence as well as a count.
    if passed and any(not any(row.get("value") in item.get("values", []) and str(item.get("quantity")) == "1"
                              for item in cart.get("cart_items", []))
                      for row in state.get("expected_rows", []) if row.get("value")):
        return False
    return bool(
        state.get("knowledge_checked") and state.get("page_checked")
        and (not state.get("requires_empty_check") or state.get("empty_check"))
        and all(r["key"] in state.get("rows", {}) and not state["rows"][r["key"]].get("retryable") for r in state.get("expected_rows", []))
        and state.get("cart_readback")
        and (not state.get("requires_screenshot") or state.get("artifacts"))
    )


def directive(state: dict) -> str:
    failures = [x for x in state.get("rows", {}).values() if x.get("result") == "failed" and not x.get("retryable")]
    if not state.get("rows"):
        return ""
    pending = [r["value"] or r["key"] for r in state["expected_rows"] if r["key"] not in state["rows"]]
    if pending:
        return ("该行已有不可重试的真实业务失败。" if failures else "已有参数行完成加购。") + "保留已有证据，直接执行剩余参数行：" + "、".join(pending) + "。不要重放已完成行、查询同一错误或切换对照商品。使用稳定定位器，将剩余行用循环/try-finally 连续完成。"
    if not state.get("cart_readback"):
        return "所有参数行已有结果。现在仅回读本会话购物车并截图，随后交付中文逐项结果。不要再次加购或继续诊断。"
    if state.get("requires_screenshot") and not state.get("artifacts"):
        return "购物车已回读，现在保存当前购物车截图后交付；不要重新加购或诊断。"
    return "证据已齐，交付测试报告。业务失败必须如实报告，不能编造购物车标识。"


def guard_command(state: dict, command: str) -> str:
    terminal = [r for r in state.get("rows", {}).values() if not r.get("retryable")]
    if not terminal:
        return ""
    target = urlparse(state["target_url"])
    for candidate in re.findall(r"\.goto\(\s*['\"]([^'\"]+)", command):
        url = urlparse(urljoin(state["target_url"], candidate))
        if url.netloc != target.netloc or ("-pdp-" in url.path and url.path != target.path):
            return directive(state)
    mutates_cart = bool((re.search(r"add[-_ ]?cart|add to cart", command, re.I) and re.search(r"\.click\(", command)) or re.search(r"method\s*:\s*['\"](?:POST|PUT|PATCH)", command, re.I))
    if not mutates_cart:
        return ""
    pending = [r for r in state["expected_rows"] if r["key"] not in state["rows"]]
    if any(r["value"] and r["value"] in command for r in pending):
        return ""
    if any(r["value"] and r["value"] in state.get("last_inputs", []) for r in pending):
        return ""
    return directive(state)


def completed_nodes(state: dict) -> list[str]:
    nodes = []
    if state.get("knowledge_checked"):
        nodes.append("fetch_conditions")
    if state.get("page_checked"):
        nodes.append("check_executable")
    if all(r["key"] in state.get("rows", {}) and not state["rows"][r["key"]].get("retryable") for r in state["expected_rows"]):
        if not state.get("requires_empty_check") or state.get("empty_check"):
            nodes.append("add_to_cart")
    if state.get("report_prepared") and delivery_ready(state):
        nodes.append("verify_deliver")
    return nodes


def report(state: dict) -> str:
    def cell(value):
        return re.sub(r"\s+", " ", str(value)).strip().replace("|", "\\|")

    failed = any(row.get("result") == "failed" for row in state["rows"].values())
    lines = ["真实测试已执行并完成核验。" + ("商城加购用例未通过。" if failed else "加购断言通过。"),
             "", f"目标商品：{state['target_url']}", "知识库来源：" + "；".join(state.get("sources") or ["本轮知识库检索，详见执行记录"]), "",
             "| 检查项 | 结果 | 真实证据 |", "|---|---|---|"]
    if state.get("requires_empty_check"):
        check = state.get("empty_check") or {}
        lines.append("| 空文本必填拦截 | " + ("通过" if check.get("passed") else "未核实") + " | " + cell("；".join(check.get("errors") or [])) + " |")
    for expected in state["expected_rows"]:
        row = state["rows"][expected["key"]]
        evidence = f"HTTP {row.get('http_status')}；{row.get('error_code') or ''}；{row.get('message') or ''}；operation_id={row['operation_id']}"
        evidence += "；请求选项：" + "、".join(row.get("request_values") or [])
        evidence += f"；数量：{row.get('request_quantity') if row.get('request_quantity') is not None else '接口未提供'}"
        evidence += "；预览：" + ("、".join(row.get("preview") or []) or "本行未采集，不判通过")
        if row.get("request_product_id") is not None:
            evidence += f"；请求商品标识：{row['request_product_id']}"
        lines.append(f"| {cell(expected['value'] or expected['key'])} | {'通过' if row['result'] == 'passed' else '失败'} | {cell(evidence)} |")
    cart = state["cart_readback"]
    source = "页面空态（HTTP 为页面响应）" if cart.get("observed_via") == "rendered_cart_page" else "接口回读"
    lines += [f"| 购物车回读 | 已核验 | {source}，HTTP {cart.get('http_status')}，商品行数 {cart.get('cart_count')}，商品金额 {cart.get('items_amount', '无商品行，未提供行金额')} |", ""]
    identifiers = dict(cart.get("identifiers") or {})
    for row in state["rows"].values():
        identifiers.update(row.get("identifiers") or {})
    lines.append("真实标识：" + ("，".join(f"{k}={v}" for k, v in identifiers.items()) or "未生成 cart_id/cart_item_id；加购被拒绝，不能用示例标识补齐。"))
    lines.append("平台执行已收敛到结果交付；商城错误单独列为业务失败。未提交订单、未支付。")
    if state.get("artifacts"):
        lines += ["", "截图与证据：" + " · ".join(f"[{a.get('name') or '截图'}]({a['url']})" for a in state["artifacts"])]
    return "\n".join(lines)


def delivered_acceptance(task):
    state = (task.facts or {}).get("browser_test") or {}
    if not state.get("report_prepared") or not delivery_ready(state) or not state.get("report_content"):
        return None
    failures = [x for x in state["rows"].values() if x.get("result") == "failed"]
    return {"ok": True, "execution_completed": True, "test_result": "failed" if failures else "passed",
            "external_failures": failures, "reasons": ["测试执行与报告交付完成；商城用例未通过。"] if failures else [],
            "report": state["report_content"], "evidence_operation_ids": [r["operation_id"] for r in state["rows"].values()]}
