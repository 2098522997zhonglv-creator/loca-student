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
        parser.add_argument(
            "--base-url",
            default="http://127.0.0.1:8000",
            help="本机运行中的服务地址，用于测试整条链路",
        )
        parser.add_argument(
            "--project-id", help="测试聊天接口用的项目 ID，默认自动取第一个项目"
        )
        parser.add_argument(
            "--skip-local",
            action="store_true",
            help="只测上游模型，跳过本机聊天接口",
        )

    def handle(self, *args, **options):
        streaming_enabled = self.check_upstream(options)
        if streaming_enabled and not options["skip_local"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("第二步：测试本机聊天接口整条链路")
            self.stdout.write("=" * 60)
            self.check_local_endpoint(options)

    def check_upstream(self, options):
        self.stdout.write("=" * 60)
        self.stdout.write("第一步：测试上游模型是否逐块返回")
        self.stdout.write("=" * 60)
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
            return False
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
                self.style.SUCCESS("判定：上游确实逐块流式返回，问题不在模型侧。")
            )

        return True

    def check_local_endpoint(self, options):
        """打一次本机运行中的聊天接口，看 SSE 事件是不是逐块到达。

        这一步才能区分「后端没真正流式输出」和「后端正常、只是浏览器里那份
        前端构建产物过旧」。
        """
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.tokens import AccessToken

        from projects.models import Project

        user = get_user_model().objects.filter(is_superuser=True).first()
        if not user:
            user = get_user_model().objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("没有任何用户，无法测试聊天接口。"))
            return

        project_id = options.get("project_id")
        if not project_id:
            project = Project.objects.first()
            if not project:
                self.stdout.write(
                    self.style.ERROR(
                        "没有任何项目，请先创建项目，或用 --project-id 指定。"
                    )
                )
                return
            project_id = project.id

        url = f"{options['base_url'].rstrip('/')}/api/orchestrator/agent-loop/"
        token = str(AccessToken.for_user(user))

        self.stdout.write(f"账号: {user.username}")
        self.stdout.write(f"项目: {project_id}")
        self.stdout.write(f"地址: {url}\n")

        payload = {
            "message": options["prompt"],
            "project_id": str(project_id),
            "use_knowledge_base": False,
            "stream": True,
        }

        start = time.monotonic()
        stream_events = 0
        first_stream_at = None

        try:
            with httpx.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=options["timeout"],
            ) as response:
                if response.status_code != 200:
                    response.read()
                    self.stdout.write(
                        self.style.ERROR(
                            f"接口返回 HTTP {response.status_code}: {response.text[:500]}"
                        )
                    )
                    return

                buffer = ""
                for piece in response.iter_text():
                    if not piece:
                        continue

                    elapsed = time.monotonic() - start
                    buffer += piece
                    lines = buffer.split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue

                        if event.get("type") == "stream":
                            stream_events += 1
                            if first_stream_at is None:
                                first_stream_at = elapsed
                        elif event.get("type") == "error":
                            self.stdout.write(
                                self.style.ERROR(
                                    f"接口返回错误: {event.get('message')}"
                                )
                            )
        except httpx.HTTPError as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"连接失败: {exc}\n"
                    "请确认服务正在运行，或用 --base-url 指定实际地址。"
                )
            )
            return

        total = time.monotonic() - start

        self.stdout.write(f"逐字事件数 : {stream_events}")
        if first_stream_at is not None:
            self.stdout.write(f"首个事件耗时: {first_stream_at:.2f}s")
        self.stdout.write(f"总耗时      : {total:.2f}s\n")

        if stream_events == 0:
            self.stdout.write(
                self.style.ERROR(
                    "判定：接口没有产出任何逐字事件，问题在后端，请把完整输出发给我。"
                )
            )
        elif first_stream_at is not None and first_stream_at > total * 0.8:
            self.stdout.write(
                self.style.ERROR(
                    "判定：事件全部堆在最后才到达，后端没有真正逐块输出，"
                    "请确认服务已重启到最新代码（runserver 带 --noreload 不会自动重载）。"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：后端整条链路确实逐块流式输出。既然上游和后端都正常，"
                    "问题就在浏览器加载的前端构建产物过旧，需要重新构建前端。"
                )
            )
