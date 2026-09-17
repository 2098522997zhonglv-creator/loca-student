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
        if not streaming_enabled:
            return

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("第二步：测试 LangChain 客户端是否拿到逐 token 数据")
        self.stdout.write("=" * 60)
        self.check_langchain_layer(options)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("第三步：测试 ainvoke 路径是否抛出 token 回调")
        self.stdout.write("=" * 60)
        self.check_invoke_callbacks(options)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("第四步：测试 LangGraph agent 层（定位中间件影响）")
        self.stdout.write("=" * 60)
        self.check_agent_layer(options)

        if not options["skip_local"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("第五步：测试本机聊天接口整条链路")
            self.stdout.write("=" * 60)
            self.check_local_endpoint(options)

    def check_invoke_callbacks(self, options):
        """检查 ainvoke 路径下模型是否逐 token 抛回调。

        create_agent 内部固定用 `await model.ainvoke(messages)`，从不用 astream。
        这条路径要不要走流式由 `_should_stream` 决定，token 则通过回调抛出，
        LangGraph 的 stream_mode="messages" 正是靠这些回调产出逐字事件。
        所以这一步和第二步的 astream 是两条不同的代码路径。
        """
        import asyncio

        from langchain_core.callbacks import AsyncCallbackHandler

        from langgraph_integration.views import create_llm_instance

        config = LLMConfig.objects.filter(is_active=True).first()
        llm = create_llm_instance(config, temperature=0.7)

        self.stdout.write(f"模型类           : {type(llm).__name__}")
        self.stdout.write(f"streaming 属性   : {getattr(llm, 'streaming', None)}")
        explicitly_set = "streaming" in getattr(llm, "model_fields_set", set())
        self.stdout.write(f"是否显式设置     : {explicitly_set}")
        self.stdout.write(
            f"disable_streaming: {getattr(llm, 'disable_streaming', None)}"
        )

        try:
            should_stream = llm._should_stream(async_api=True)
            self.stdout.write(f"_should_stream   : {should_stream}")
        except Exception as exc:
            should_stream = None
            self.stdout.write(f"_should_stream   : 无法判断（{exc}）")

        class TokenCounter(AsyncCallbackHandler):
            def __init__(self):
                self.tokens = 0
                self.first_at = None
                self.start = time.monotonic()

            async def on_llm_new_token(self, token, **kwargs):
                self.tokens += 1
                if self.first_at is None:
                    self.first_at = time.monotonic() - self.start

        counter = TokenCounter()

        async def run():
            return await llm.ainvoke(
                options["prompt"], config={"callbacks": [counter]}
            )

        try:
            asyncio.run(run())
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"调用失败: {type(exc).__name__}: {exc}")
            )
            return

        self.stdout.write(f"token 回调次数   : {counter.tokens}")
        if counter.first_at is not None:
            self.stdout.write(f"首个回调耗时     : {counter.first_at:.2f}s")
        self.stdout.write("")

        if counter.tokens > 1:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：ainvoke 路径确实逐 token 抛回调，"
                    "问题在 LangGraph 把回调转成 messages 事件这一段。"
                )
            )
        elif should_stream is False:
            self.stdout.write(
                self.style.ERROR(
                    "判定：_should_stream 为 False，ainvoke 走了非流式路径，"
                    "所以 create_agent 拿不到任何 token 回调。"
                    "对照上面几行属性即可看出是哪个条件没满足。"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "判定：ainvoke 没有抛出逐 token 回调，"
                    "这正是 create_agent 只产出一个事件的直接原因。"
                )
            )

    def check_agent_layer(self, options):
        """在进程内用 create_agent + astream 数 token 事件。

        聊天接口正是这样产出逐字内容的。分别测不挂中间件和挂上中间件两种情况：
        只实现了 wrap_model_call 的中间件会把模型调用包成一次性调用，
        stream_mode="messages" 就拿不到逐 token 事件。
        """
        import asyncio

        from langchain.agents import create_agent

        from langgraph_integration.views import create_llm_instance
        from orchestrator_integration.middleware_config import (
            get_model_retry_middleware,
            get_tool_retry_middleware,
        )

        for name, version in self._package_versions().items():
            self.stdout.write(f"{name}: {version}")
        self.stdout.write("")

        config = LLMConfig.objects.filter(is_active=True).first()
        llm = create_llm_instance(config, temperature=0.7)

        async def probe(middleware):
            agent = create_agent(llm, [], middleware=middleware)
            start = time.monotonic()
            events = 0
            first_at = None
            async for mode, chunk in agent.astream(
                {"messages": [("user", options["prompt"])]},
                stream_mode=["messages"],
            ):
                if mode != "messages" or not isinstance(chunk, tuple):
                    continue
                token = chunk[0]
                if not getattr(token, "content", ""):
                    continue
                events += 1
                if first_at is None:
                    first_at = time.monotonic() - start
            return events, first_at, time.monotonic() - start

        cases = [
            ("不挂任何中间件", []),
            ("只挂 ModelRetryMiddleware", [get_model_retry_middleware()]),
            ("只挂 ToolRetryMiddleware", [get_tool_retry_middleware()]),
        ]

        # 聊天接口真正用的是这一组，包含按配置启用的上下文压缩、工具审批等
        try:
            from orchestrator_integration.middleware_config import (
                get_middleware_from_config,
            )

            real = get_middleware_from_config(config, llm)
            label = "该配置实际使用的全部中间件（" + "、".join(
                type(mw).__name__ for mw in real
            ) + "）"
            cases.append((label, real))
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"取实际中间件组合失败，跳过该用例: {type(exc).__name__}: {exc}"
                )
            )

        results = {}
        for label, middleware in cases:
            try:
                events, first_at, total = asyncio.run(probe(middleware))
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"[{label}] 调用失败: {type(exc).__name__}: {exc}")
                )
                continue

            results[label] = events
            detail = f"逐字事件 {events} 个"
            if first_at is not None:
                detail += f"，首个 {first_at:.2f}s"
            detail += f"，总耗时 {total:.2f}s"
            self.stdout.write(f"[{label}] {detail}")

        self.stdout.write("")

        bare = results.get("不挂任何中间件")
        if bare is None:
            return

        if bare <= 1:
            self.stdout.write(
                self.style.ERROR(
                    "判定：连不挂中间件都拿不到逐 token 事件，"
                    "问题出在 langchain/langgraph 版本上，请把上面的版本号发给我。"
                )
            )
            return

        broken = [
            label
            for label, events in results.items()
            if label != "不挂任何中间件" and events <= 1
        ]
        if broken:
            self.stdout.write(
                self.style.ERROR(
                    "判定：以下中间件会吃掉逐 token 事件，这就是流式失效的原因："
                    + "、".join(broken)
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：agent 层和这些中间件都能逐 token 输出，"
                    "断点在聊天接口自身的处理流程里。"
                )
            )

    def _package_versions(self):
        from importlib.metadata import PackageNotFoundError, version

        names = ["langchain", "langchain-core", "langchain-openai", "langgraph"]
        versions = {}
        for name in names:
            try:
                versions[name] = version(name)
            except PackageNotFoundError:
                versions[name] = "未安装"
        return versions

    def check_langchain_layer(self, options):
        """用项目自己的 create_llm_instance 直接 astream。

        上游对裸 HTTP 请求是流式、而整条链路却不是流式时，断点很可能在这一层：
        LangChain 发出的请求会多带一些参数（例如用于统计用量的 stream_options），
        某些网关遇到后会退化成一次性返回。
        """
        import asyncio

        from langgraph_integration.views import create_llm_instance

        config = LLMConfig.objects.filter(is_active=True).first()
        llm = create_llm_instance(config, temperature=0.7)

        async def collect():
            start = time.monotonic()
            chunks = 0
            first_at = None
            text_length = 0
            async for chunk in llm.astream(options["prompt"]):
                content = getattr(chunk, "content", "")
                if not content:
                    continue
                chunks += 1
                text_length += len(content)
                if first_at is None:
                    first_at = time.monotonic() - start
            return chunks, first_at, text_length, time.monotonic() - start

        try:
            chunks, first_at, text_length, total = asyncio.run(collect())
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"调用失败: {type(exc).__name__}: {exc}")
            )
            if "generation chunks" in str(exc):
                self.stdout.write(
                    self.style.ERROR(
                        "这个报错本身就说明网关没有给 LangChain 返回流式数据，"
                        "对照第一步的两次结果即可确认是哪个请求参数导致的。"
                    )
                )
            return

        self.stdout.write(f"收到分块数: {chunks}")
        if first_at is not None:
            self.stdout.write(f"首块耗时  : {first_at:.2f}s")
        self.stdout.write(f"正文长度  : {text_length}")
        self.stdout.write(f"总耗时    : {total:.2f}s\n")

        if chunks <= 1:
            self.stdout.write(
                self.style.ERROR(
                    "判定：LangChain 只拿到一整块，说明网关对 LangChain 发出的请求"
                    "退化成了非流式。它与第一步的差别是多带了 stream_options"
                    "（用于统计 token 用量），这是最可能的原因。"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "判定：LangChain 这一层拿到了逐 token 数据，断点在更上层。"
                )
            )

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

        base_payload = {
            "model": config.name,
            "messages": [{"role": "user", "content": options["prompt"]}],
            "stream": True,
        }

        plain = self._measure_sse(url, headers, base_payload, options["timeout"])
        self._report_sse("只带 stream=true", plain)

        # LangChain 为了统计 token 用量会额外带上 stream_options，部分网关遇到它
        # 会退化成一次性返回。这里单独比一次，以便确认是不是这个参数导致的。
        with_usage = self._measure_sse(
            url,
            headers,
            {**base_payload, "stream_options": {"include_usage": True}},
            options["timeout"],
        )
        self._report_sse("额外带 stream_options", with_usage)

        if plain["content_chunks"] <= 1:
            self.stdout.write(
                self.style.ERROR(
                    "判定：上游一次性返回，不是流式。"
                    "请检查模型服务或网关是否支持 stream=true。"
                )
            )
        elif with_usage["content_chunks"] <= 1:
            self.stdout.write(
                self.style.ERROR(
                    "判定：带上 stream_options 后上游就退化成一次性返回了。"
                    "LangChain 统计用量时正是这样发请求的，这就是流式失效的原因。"
                )
            )
        elif plain["reasoning_chunks"] > plain["content_chunks"]:
            self.stdout.write(
                self.style.WARNING(
                    "判定：上游是流式的，但思考块远多于正文块，"
                    "说明这是推理型模型在输出思考过程，界面上会表现为长时间空白后整段出现。"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("判定：上游两种请求都逐块流式返回，问题不在模型侧。")
            )

        return True

    def _measure_sse(self, url, headers, payload, timeout):
        """发一次 SSE 请求，记录每块的到达时刻。

        必须按网络分片逐块读取：requests 的 iter_lines 会攒满 512 字节才返回，
        iter_content(chunk_size=None) 又会一直读到 EOF，两者都会把真流式测成
        一次性返回。
        """
        start = time.monotonic()
        result = {
            "content_chunks": 0,
            "reasoning_chunks": 0,
            "total_chunks": 0,
            "first_content_at": None,
            "total": 0.0,
            "content_type": "",
        }

        try:
            with httpx.stream(
                "POST", url, headers=headers, json=payload, timeout=timeout
            ) as response:
                if response.status_code != 200:
                    response.read()
                    raise CommandError(
                        f"上游返回 HTTP {response.status_code}: {response.text[:500]}"
                    )

                result["content_type"] = response.headers.get("Content-Type", "")

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

                        result["total_chunks"] += 1
                        try:
                            delta = json.loads(data)["choices"][0].get("delta", {})
                        except (ValueError, KeyError, IndexError):
                            continue

                        # 推理型模型把思考过程放在 reasoning_content，前端只渲染
                        # content，表现出来就是「先长时间没反应，然后答案整段冒出来」
                        if delta.get("reasoning_content"):
                            result["reasoning_chunks"] += 1
                        if delta.get("content"):
                            result["content_chunks"] += 1
                            if result["first_content_at"] is None:
                                result["first_content_at"] = elapsed
        except httpx.HTTPError as exc:
            raise CommandError(f"请求失败: {exc}") from exc

        result["total"] = time.monotonic() - start
        return result

    def _report_sse(self, label, result):
        self.stdout.write(f"[{label}]")
        self.stdout.write(f"  Content-Type    : {result['content_type']}")
        if "event-stream" not in result["content_type"]:
            self.stdout.write(
                self.style.WARNING("  上游没有按 SSE 返回，说明它忽略了 stream=true。")
            )
        self.stdout.write(f"  数据块总数      : {result['total_chunks']}")
        self.stdout.write(f"  正文块 content  : {result['content_chunks']}")
        self.stdout.write(f"  思考块 reasoning: {result['reasoning_chunks']}")
        if result["first_content_at"] is not None:
            self.stdout.write(f"  首个正文块耗时  : {result['first_content_at']:.2f}s")
        self.stdout.write(f"  总耗时          : {result['total']:.2f}s\n")

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
