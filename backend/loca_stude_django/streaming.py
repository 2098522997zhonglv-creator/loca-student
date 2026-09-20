"""
SSE 流式响应工具。

背景：Django 5 的 StreamingHttpResponse 在 WSGI（runserver / gunicorn 等同步
服务器）下拿到 async 生成器时，会在 __iter__ 里用 async_to_sync 把整个流一次性
收集成 list 再输出（见 django/http/response.py）。结果是浏览器等到全部内容生成
完才收到第一个字节，SSE 退化为一次性响应。

这里把 async 生成器放到独立线程的事件循环中运行，通过队列逐块交给 WSGI，使
StreamingHttpResponse 拿到的是真正的同步迭代器。

注意：async 生成器体在首次迭代时才执行，因此其内部创建的所有异步资源
（checkpointer、MCP 会话、agent.astream 等）都会绑定到这个新事件循环，
不会与视图自身的循环冲突。
"""

import asyncio
import logging
import queue
import threading
from typing import AsyncIterator, Callable, Iterator

from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)

_STREAM_END = object()

# 队列容量：限制生成快于下游消费时的内存占用，同时保留足够缓冲避免频繁阻塞
_QUEUE_MAXSIZE = 256


def iter_async_generator(
    make_async_iterator: Callable[[], AsyncIterator],
) -> Iterator:
    """把 async 生成器转成同步迭代器，逐块产出而不缓冲整个流。

    make_async_iterator 必须是一个可调用对象（而非已创建的生成器），
    以保证生成器在目标事件循环所在线程中被创建和消费。
    """
    chunks: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAXSIZE)

    def pump() -> None:
        async def drain() -> None:
            try:
                async for chunk in make_async_iterator():
                    chunks.put(chunk)
            except BaseException as exc:  # 交给消费侧抛出，保留原始堆栈
                chunks.put(exc)
            finally:
                chunks.put(_STREAM_END)

        try:
            asyncio.run(drain())
        except BaseException:
            logger.exception("SSE stream pump crashed")
            chunks.put(_STREAM_END)

    worker = threading.Thread(target=pump, name="sse-stream-pump", daemon=True)
    worker.start()

    while True:
        chunk = chunks.get()
        if chunk is _STREAM_END:
            return
        if isinstance(chunk, BaseException):
            raise chunk
        yield chunk


def sse_response(
    make_async_iterator: Callable[[], AsyncIterator],
    status: int = 200,
) -> StreamingHttpResponse:
    """构造一个在 WSGI 下也能真正逐块输出的 SSE 响应。"""
    response = StreamingHttpResponse(
        iter_async_generator(make_async_iterator),
        content_type="text/event-stream; charset=utf-8",
        status=status,
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
