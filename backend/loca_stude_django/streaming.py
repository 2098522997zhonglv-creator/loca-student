"""
SSE 流式响应工具。

背景：
- Django 5 在 WSGI（runserver / gunicorn）下若直接塞 async 生成器，会在
  __iter__ 里用 async_to_sync 把整段攒齐再输出，SSE 退化成一次性响应。
- 本机生产路径是 Daphne（ASGI，见 django.channels.server 日志）。ASGI 下应直接
  喂 async 迭代器，才能真正逐块刷出；若再套「独立线程 + asyncio.run」同步泵，
  反而可能与请求事件循环错位，表现为 diagnose 第四步通、第五步/浏览器仍整段出。

策略：
- 已有 running loop（典型 ASGI/Daphne）→ 原生 async StreamingHttpResponse
- 无 running loop（典型同步 WSGI 视图）→ 线程泵转同步迭代器
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


def _under_asgi() -> bool:
    """当前是否已在事件循环中（ASGI 请求处理路径）。"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def sse_response(
    make_async_iterator: Callable[[], AsyncIterator],
    status: int = 200,
) -> StreamingHttpResponse:
    """构造 SSE 响应：ASGI 用原生 async 流，WSGI 用线程泵。"""
    if _under_asgi():

        async def async_body():
            async for chunk in make_async_iterator():
                yield chunk

        streaming_content = async_body()
        logger.debug("SSE response using native ASGI async iterator")
    else:
        streaming_content = iter_async_generator(make_async_iterator)
        logger.debug("SSE response using WSGI thread pump")

    response = StreamingHttpResponse(
        streaming_content,
        content_type="text/event-stream; charset=utf-8",
        status=status,
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
