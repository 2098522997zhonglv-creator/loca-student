"""诊断流式输出：直接向已激活的 LLM 端点发一次 stream=true 请求，逐块打时间戳。

用途是把「前端看不到逐字输出」这个现象拆开定位：如果上游本身就是一次性返回，
那么后端 SSE 桥接和前端渲染做得再对也不会有逐字效果。
"""

import json
import time

import httpx
from django.core.management.base import BaseCommand, CommandError

from langgraph_integration.models import LLMConfig


class Command(BaseCommand):
    help = "检测当前激活的 LLM 配置是否真的逐 token 流式返回"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prompt",
            default="请用大约一百字介绍一下你自己。",
            help="发送给模型的测试问题，内容越长越容易观察到分块",
        )
        parser.add_argument(
            "--timeout", type=int, default=120, help="请求超时时间（秒）"
        )

    def handle(self, *args, **options):
        config = LLMConfig.objects.filter(is_active=True).first()
        if not config:
            raise CommandError("没有找到已激活的 LLM 配置，请先在界面上激活一个配置。")

        url = config.api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        payload = {
            "model": config.name,
            "messages": [{"role": "user", "content": options["prompt"]}],
            "stream": True,
        }

        self.stdout.write(f"配置: {config.config_name}（{config.provider}）")
        self.stdout.write(f"模型: {config.name}")
        self.stdout.write(f"地址: {url}")

        # 前端完全依据这个字段二选一：关闭时走非流式接口，等完整回复再一次性渲染，
        # 此时后端的 SSE 逐块输出根本不会被用到。
        self.stdout.write(f"配置项 enable_streaming: {config.enable_streaming}")
        if not config.enable_streaming:
            self.stdout.write(
                self.style.ERROR(
                    "\n这就是看不到逐字输出的原因：该配置关闭了流式输出，"
                    "前端会走非流式接口。请在 LLM 配置里打开「启用流式输出」后重试。"
                )
            )
            return
        self.stdout.write("")

        start = time.monotonic()
        first_content_at = None
        content_chunks = 0
        reasoning_chunks = 0
        arrival_times = []

        # 必须按网络分片逐块读取。requests 的 iter_lines 会攒满 512 字节才返回，
        # iter_content(chunk_size=None) 又会一直读到 EOF，两者都会把真流式测成一次性返回。
        try:
            with httpx.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=options["timeout"],
            ) as response:
                if response.status_code != 200:
                    response.read()
                    raise CommandError(
                        f"上游返回 HTTP {response.status_code}: {response.text[:500]}"
                    )

                content_type = response.headers.get("Content-Type", "")
                self.stdout.write(f"响应 Content-Type: {content_type}")
                if "event-stream" not in content_type:
                    self.stdout.write(
                        self.style.WARNING(
                            "上游没有按 SSE 返回，说明它忽略了 stream=true，"
                            "这就是看不到逐字输出的原因。"
                        )
                    )

                buffer = ""
                done = False
                for piece in response.iter_text():
                    if done:
                        break
                    if not piece:
                        continue

                    elapsed = time.monotonic() - start
                    buffer += piece
                    lines = buffer.split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            done = True
                            break

                        arrival_times.append(elapsed)
                        try:
                            delta = json.loads(data)["choices"][0].get("delta", {})
                        except (ValueError, KeyError, IndexError):
                            continue

                        # 推理型模型把思考过程放在 reasoning_content，前端只渲染
                        # content，表现出来就是「先长时间没反应，然后答案整段冒出来」
                        if delta.get("reasoning_content"):
                            reasoning_chunks += 1
                        if delta.get("content"):
                            content_chunks += 1
                            if first_content_at is None:
                                first_content_at = elapsed
        except httpx.HTTPError as exc:
            raise CommandError(f"请求失败: {exc}") from exc

        total = time.monotonic() - start

        self.stdout.write("")
        self.stdout.write(f"数据块总数      : {len(arrival_times)}")
        self.stdout.write(f"正文块 content  : {content_chunks}")
        self.stdout.write(f"思考块 reasoning: {reasoning_chunks}")
        if first_content_at is not None:
            self.stdout.write(f"首个正文块耗时  : {first_content_at:.2f}s")
        self.stdout.write(f"总耗时          : {total:.2f}s")
        self.stdout.write("")

        if content_chunks <= 1:
            self.stdout.write(
                self.style.ERROR(
                    "判定：上游一次性返回，不是流式。请检查模型服务或网关是否支持 stream=true。"
                )
            )
        elif first_content_at is not None and first_content_at > total * 0.6:
            self.stdout.write(
                self.style.WARNING(
                    "判定：上游是流式的，但正文开始得很晚。若思考块数量很多，"
                    "说明这是推理型模型在输出思考过程，界面上会表现为长时间空白后整段出现。"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：上游确实逐块流式返回。问题不在模型侧，"
                    "请确认后端服务已重启到最新代码（runserver 带 --noreload 不会自动重载）。"
                )
            )
