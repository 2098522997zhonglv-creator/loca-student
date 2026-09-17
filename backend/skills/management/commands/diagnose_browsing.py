"""诊断 AI 访问网址的能力。

对话里让 AI 打开一个网址时，它只能借助技能来做。这些技能的运行前提差别很大：
url-reader 只用 Python 标准库，而浏览器类技能需要 Node、Playwright 或一个开着
远程调试端口的 Chrome。这个命令逐项检查这些前提，并可选地实际抓一次目标网址，
用来区分「没有可用的访问能力」和「能访问但页面是 JS 渲染、抓不到内容」。
"""

import json
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from skills.models import Skill

BROWSING_SKILLS = ["url-reader", "browser-use", "playwright-skill", "playwright-cli"]

# Chrome 远程调试的默认端口，browser-use 靠它接管浏览器
CDP_PORT = 9222


class Command(BaseCommand):
    help = "检查 AI 访问网址所需的技能与运行环境"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url", help="实际抓一次这个网址，用来判断能否取到正文"
        )
        parser.add_argument(
            "--timeout", type=int, default=30, help="抓取超时时间（秒）"
        )

    def handle(self, *args, **options):
        self.check_skills()
        self.check_runtime()
        if options["url"]:
            self.check_url(options["url"], options["timeout"])
        else:
            self.stdout.write(
                "\n提示：加上 --url <原型地址> 可以实际抓一次，看能否取到正文。"
            )

    def check_skills(self):
        self.stdout.write("=" * 60)
        self.stdout.write("一、技能是否已导入并启用")
        self.stdout.write("=" * 60)

        for name in BROWSING_SKILLS:
            skill = Skill.objects.filter(name=name).first()
            if not skill:
                self.stdout.write(
                    self.style.WARNING(f"{name:18}: 未导入（AI 看不到这个技能）")
                )
            elif not skill.is_active:
                self.stdout.write(
                    self.style.WARNING(f"{name:18}: 已导入但未启用")
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{name:18}: 已启用"))

        self.stdout.write(
            "\n未导入的可以用 python manage.py init_skills 同步。"
        )

    def check_runtime(self):
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("二、运行环境")
        self.stdout.write("=" * 60)

        # url-reader 只用 Python 标准库，所以一定可用
        self.stdout.write(
            self.style.SUCCESS("url-reader     : 只用 Python 标准库，无需额外环境")
        )

        node = shutil.which("node")
        if node:
            try:
                version = subprocess.run(
                    [node, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout.strip()
            except (subprocess.SubprocessError, OSError):
                version = "无法执行"
            self.stdout.write(self.style.SUCCESS(f"node           : {version}（{node}）"))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "node           : 不在 PATH 里，playwright 类技能无法运行"
                )
            )

        # playwright-skill 需要自己目录下装好依赖
        skill_dir = Path(settings.PROJECT_ROOT) / "bundled_skills" / "playwright-skill"
        modules = skill_dir / "node_modules"
        if not skill_dir.exists():
            self.stdout.write("playwright-skill: 目录不存在")
        elif modules.exists():
            self.stdout.write(
                self.style.SUCCESS("playwright-skill: node_modules 已安装")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "playwright-skill: 缺 node_modules，首次调用会现装，容易超时。"
                    f"可先手动执行 npm install（目录 {skill_dir}）"
                )
            )

        # browser-use 要接管一个开着远程调试端口的 Chrome
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            reachable = sock.connect_ex(("127.0.0.1", CDP_PORT)) == 0

        if reachable:
            self.stdout.write(
                self.style.SUCCESS(f"browser-use    : 检测到 {CDP_PORT} 端口的 Chrome")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"browser-use    : {CDP_PORT} 端口没有 Chrome，该技能用不了。"
                    "需要用 --remote-debugging-port=9222 启动 Chrome。"
                )
            )

    def check_url(self, url, timeout):
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("三、实际抓取目标网址")
        self.stdout.write("=" * 60)
        self.stdout.write(f"地址: {url}\n")

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                body = response.read(400_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            self.stdout.write(
                self.style.ERROR(f"判定：服务器返回 HTTP {exc.code}，无法取到内容。")
            )
            if exc.code in (401, 403):
                self.stdout.write("这个地址需要登录或有访问限制，AI 也拿不到。")
            return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            self.stdout.write(self.style.ERROR(f"判定：连不上（{reason}）。"))
            self.stdout.write(
                "说明这台服务器本身访问不了该地址，请先确认网络、内网可达性或代理。"
            )
            return

        text = self._visible_text(body)
        self.stdout.write(f"HTTP 状态    : {status}")
        self.stdout.write(f"Content-Type : {content_type}")
        self.stdout.write(f"HTML 长度    : {len(body)}")
        self.stdout.write(f"可见文字长度 : {len(text)}\n")

        if len(text) >= 200:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：能直接取到正文，url-reader 足以读懂这个地址。"
                )
            )
            return

        self.stdout.write(
            self.style.ERROR(
                "判定：能连通但几乎没有正文，说明页面内容靠 JavaScript 渲染"
                "（原型工具大多如此）。url-reader 只会拿到空壳，"
                "必须用浏览器类技能，也就是上面第二节里那些前提要先满足。"
            )
        )

    @staticmethod
    def _visible_text(html_text):
        """粗略估算可见文字，用来判断是不是 JS 渲染的空壳。"""
        import re

        without_script = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I
        )
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_script)).strip()
