from __future__ import annotations

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class ReasoningCompatibleChatDeepSeek(ChatDeepSeek):
    """DeepSeek 模型包装，确保 thinking 模式会回传 reasoning_content。"""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" not in payload:
            return payload

        messages = self._convert_input(input_).to_messages()
        for original_message, payload_message in zip(messages, payload["messages"]):
            if not isinstance(original_message, AIMessage):
                continue

            reasoning_content = original_message.additional_kwargs.get(
                "reasoning_content"
            )
            if reasoning_content in (None, ""):
                continue

            payload_message["reasoning_content"] = reasoning_content

        return payload
