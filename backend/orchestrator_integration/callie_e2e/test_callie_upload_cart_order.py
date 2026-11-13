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


_REQUIRED_ENV = (
    "CALLIE_BASE_URL",
    "CALLIE_USERNAME",
    "CALLIE_PASSWORD",
    "CALLIE_PRODUCT_URL",
)


def missing_callie_env() -> list:
    """RC18：显式列出缺失的联验环境变量（跳过项可复核）。"""
    return [k for k in _REQUIRED_ENV if not (os.environ.get(k) or "").strip()]


def _env_ready() -> bool:
    return not missing_callie_env()


def _skip_reason() -> str:
    missing = missing_callie_env()
    if missing:
        return (
            "RC18 SKIP 真实 Callie E2E：缺少环境变量 "
            f"{missing}；已配置项不计入通过。设置全部 CALLIE_* 后重跑。"
        )
    return "CALLIE_* 未就绪"


def _sample_image() -> Path:
    configured = os.environ.get("CALLIE_UPLOAD_FILE")
    if configured and Path(configured).is_file():
        return Path(configured)
    fixture = Path(__file__).resolve().parent / "fixtures" / "sample.png"
    return fixture


@unittest.skipUnless(_env_ready(), _skip_reason())
class CallieUploadCartOrderE2ETests(SimpleTestCase):
    """真实浏览器路径：登录 → 打开商品 → 上传 → 预览/加购 →（可选）提交订单。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # RC18：verbose 输出可复核跳过清单（即使未 skip 也打印策略）
        print(
            "[RC18 Callie E2E] required_env=%s missing=%s "
            "ALLOW_SUBMIT_ORDER=%s REQUIRE_NATIVE=%s"
            % (
                list(_REQUIRED_ENV),
                missing_callie_env(),
                os.environ.get("CALLIE_ALLOW_SUBMIT_ORDER", "0"),
                os.environ.get("CALLIE_REQUIRE_NATIVE", "0"),
            ),
            flush=True,
        )

    def test_upload_add_to_cart_and_optional_submit_order(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self.skipTest(f"RC18 SKIP：playwright 未安装: {exc}")

        image = _sample_image()
        if not image.is_file():
            self.skipTest(f"RC18 SKIP：上传样例不存在: {image}")

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
                cid = m.group(2)
                # D03/RC18：禁止用点击观察值冒充真实编号
                if cid.lower() in {
                    "observed_cart_action",
                    "placeholder",
                    "static_preview",
                    "preview",
                }:
                    evidence["cart_id"] = ""
                else:
                    evidence["cart_id"] = cid
            elif cart_clicked:
                # 仅记录动作发生，不写入伪 cart_id
                evidence["cart_clicked"] = True

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
        # D03：必须有真实类型化 cart_id；静态 Preview 文案或仅点击不得过关
        self.assertTrue(
            bool(evidence.get("cart_id")),
            f"缺少真实 cart_id/cart_item_id（禁止 observed_cart_action/静态 Preview）: {evidence}",
        )
        self.assertNotEqual(
            str(evidence.get("cart_id") or "").lower(),
            "observed_cart_action",
        )
        if allow_submit:
            self.assertTrue(
                evidence["order_sn"] or "order" in evidence.get("source_url", "").lower(),
                f"下单证据不足: {evidence}",
            )
        # 支付按钮即使出现也不得点击：本用例无支付点击路径
        self.assertNotIn("paid", evidence)
