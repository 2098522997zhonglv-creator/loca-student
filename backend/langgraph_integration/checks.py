"""启动检查。"""

import sys

from django.core.checks import Warning, register


@register()
def check_python_supports_streaming(app_configs, **kwargs):
    """Python 3.11 以下会导致逐 token 流式输出静默失效。

    asyncio.create_task 的 context 参数是 3.11 才加的，LangGraph 在更低版本上
    会直接丢弃 context（见其 _internal/_future.py 里的 CONTEXT_NOT_SUPPORTED）。
    模型的 token 回调因此传不到 stream_mode="messages" 的接收端，聊天只能在
    整段回复生成完后一次性显示。功能不报错，只是流式没了，很难察觉。
    """
    if sys.version_info >= (3, 11):
        return []

    current = ".".join(str(part) for part in sys.version_info[:3])
    return [
        Warning(
            f"当前 Python {current} 会导致 AI 回复无法逐字流式输出。",
            hint=(
                "请把运行环境升级到 Python 3.11 及以上（见 environment.yml）。"
                "可用 python manage.py diagnose_streaming 复核。"
            ),
            id="langgraph_integration.W001",
        )
    ]
