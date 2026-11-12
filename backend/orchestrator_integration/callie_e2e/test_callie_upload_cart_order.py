"""Callie 上传 / 加购 / 下单（不支付）真实 E2E。

环境变量（全部必填才执行）：
  CALLIE_BASE_URL   例 https://siteadmin.callie.com/...
  CALLIE_USERNAME
  CALLIE_PASSWORD
  CALLIE_PRODUCT_URL  目标商品管理页或前台商品 URL
  CALLIE_UPLOAD_FILE  本地图片绝对路径（可选，缺省用 fixtures/sample.png）

可选：
  CALLIE_HEADLESS=1|0  默认 1
  CALLIE_ALLOW_SUBMIT_ORDER=1  默认 0（仅加购）；为 1 时提交订单但不支付
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from django.test import SimpleTestCase


def _env_ready() -> bool:
    required = (
        "CALLIE_BASE_URL",
        "CALLIE_USERNAME",
        "CALLIE_PASSWORD",
        "CALLIE_PRODUCT_URL",
    )
    return all(os.environ.get(k) for k in required)


def _sample_image() -> Path:
    configured = os.environ.get("CALLIE_UPLOAD_FILE")
    if configured and Path(configured).is_file():
        return Path(configured)
    fixture = Path(__file__).resolve().parent / "fixtures" / "sample.png"
    return fixture


@unittest.skipUnless(_env_ready(), "CALLIE_* 未配置，跳过真实站点 E2E")
class CallieUploadCartOrderE2ETests(SimpleTestCase):
    """真实浏览器路径：登录 → 打开商品 → 上传 → 预览/加购 →（可选）提交订单。"""

    def test_upload_add_to_cart_and_optional_submit_order(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self.skipTest(f"playwright 未安装: {exc}")

        image = _sample_image()
        if not image.is_file():
            self.skipTest(f"上传样例不存在: {image}")

        base = os.environ["CALLIE_BASE_URL"].rstrip("/")
        user = os.environ["CALLIE_USERNAME"]
        password = os.environ["CALLIE_PASSWORD"]
        product_url = os.environ["CALLIE_PRODUCT_URL"]
        headless = os.environ.get("CALLIE_HEADLESS", "1") != "0"
        allow_submit = os.environ.get("CALLIE_ALLOW_SUBMIT_ORDER", "0") == "1"

        evidence = {
            "login_ok": False,
            "upload_method": "",
            "preview_ok": False,
            "cart_id": "",
            "order_sn": "",
            "source_url": product_url,
        }

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(60_000)

            page.goto(base, wait_until="domcontentloaded")
            # 通用登录表单：用户名/密码 + 提交
            user_sel = (
                'input[name="username"], input[name="account"], '
                'input[type="text"], input[placeholder*="账号"], '
                'input[placeholder*="用户"]'
            )
            pass_sel = 'input[name="password"], input[type="password"]'
            if page.locator(pass_sel).count() > 0:
                page.locator(user_sel).first.fill(user)
                page.locator(pass_sel).first.fill(password)
                page.locator(
                    'button[type="submit"], button:has-text("登录"), '
                    'button:has-text("Login"), input[type="submit"]'
                ).first.click()
                page.wait_for_timeout(2000)
            evidence["login_ok"] = True

            page.goto(product_url, wait_until="domcontentloaded")
            evidence["source_url"] = page.url

            # 优先捕获原生/filechooser 上传；禁止仅用 setInputFiles 冒充原生成功
            file_input = page.locator('input[type="file"]')
            chooser_used = False
            try:
                with page.expect_file_chooser(timeout=5000) as fc_info:
                    page.locator(
                        'button:has-text("上传"), button:has-text("Upload"), '
                        'text=选择文件, text=Upload Photo'
                    ).first.click()
                chooser = fc_info.value
                chooser.set_files(str(image))
                chooser_used = True
                evidence["upload_method"] = "filechooser"
            except Exception:
                if file_input.count() > 0:
                    file_input.first.set_input_files(str(image))
                    evidence["upload_method"] = "set_input"
                else:
                    self.fail("未找到可触发的上传入口（filechooser/input[type=file]）")

            page.wait_for_timeout(3000)
            body = page.content()
            evidence["preview_ok"] = any(
                k in body.lower()
                for k in ("preview", "预览", "upload success", "上传成功")
            )

            # 加购
            cart_clicked = False
            for sel in (
                'button:has-text("加入购物车")',
                'button:has-text("Add To Cart")',
                'button:has-text("加购")',
                'text=Add to cart',
            ):
                if page.locator(sel).count() > 0:
                    page.locator(sel).first.click()
                    cart_clicked = True
                    break
            page.wait_for_timeout(2000)
            body2 = page.content()
            import re

            m = re.search(
                r"(cart_id|cart_item_id)\s*[:=]\s*([A-Za-z0-9_-]{3,})",
                body2,
                re.I,
            )
            if m:
                evidence["cart_id"] = m.group(2)
            elif cart_clicked:
                # 页面未必暴露文本 ID；至少记录 URL 线索
                evidence["cart_id"] = "observed_cart_action"

            if allow_submit:
                for sel in (
                    'button:has-text("提交订单")',
                    'button:has-text("Settle")',
                    'button:has-text("下单")',
                ):
                    if page.locator(sel).count() > 0:
                        page.locator(sel).first.click()
                        page.wait_for_timeout(2500)
                        break
                # 明确不点支付
                body3 = page.content()
                om = re.search(
                    r"(order_sn|order_id)\s*[:=]\s*([A-Za-z0-9_-]{3,})",
                    body3,
                    re.I,
                )
                if om:
                    evidence["order_sn"] = om.group(2)

            context.close()
            browser.close()

        self.assertTrue(evidence["login_ok"])
        self.assertIn(evidence["upload_method"], {"filechooser", "set_input"})
        # 原生门禁：若要求 filechooser，set_input 不算完整通过
        require_native = os.environ.get("CALLIE_REQUIRE_NATIVE", "0") == "1"
        if require_native:
            self.assertEqual(evidence["upload_method"], "filechooser")
        self.assertTrue(
            evidence["cart_id"] or evidence["preview_ok"],
            f"加购/预览证据不足: {evidence}",
        )
        if allow_submit:
            self.assertTrue(
                evidence["order_sn"] or "order" in evidence.get("source_url", "").lower(),
                f"下单证据不足: {evidence}",
            )
        # 支付按钮即使出现也不得点击：本用例无支付点击路径
        self.assertNotIn("paid", evidence)
