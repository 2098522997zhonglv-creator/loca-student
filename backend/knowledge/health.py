"""知识库依赖服务（Qdrant / Embedding）连通性诊断，只在检索出错时调用。"""

import os
import socket
from typing import List
from urllib.parse import urlparse

_CONNECTION_ERROR_MARKERS = (
    "10061",
    "积极拒绝",
    "actively refused",
    "connection refused",
    "connecterror",
    "failed to establish a new connection",
    "max retries exceeded",
)


def is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionError):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _CONNECTION_ERROR_MARKERS)


def _port_open(url: str, timeout: float = 1.0) -> bool:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname
    if not host:
        return True
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def describe_qdrant_target() -> str:
    qdrant_url = os.environ.get("QDRANT_URL", "").strip()
    if qdrant_url:
        return f"远程 Qdrant 服务 {qdrant_url}"
    return "本地嵌入式 Qdrant"


def diagnose_unreachable_services() -> List[str]:
    down: List[str] = []
    qdrant_url = os.environ.get("QDRANT_URL", "").strip()
    if qdrant_url and not _port_open(qdrant_url):
        down.append(f"Qdrant 向量库（{qdrant_url}）")
    try:
        from .models import KnowledgeGlobalConfig

        embedding_url = (KnowledgeGlobalConfig.get_config().api_base_url or "").strip()
    except Exception:
        embedding_url = ""
    if embedding_url and not _port_open(embedding_url):
        down.append(f"Embedding 服务（{embedding_url}）")
    return down


def build_kb_unavailable_message(exc: BaseException = None) -> str:
    down = diagnose_unreachable_services()
    if down:
        reason = "、".join(down) + " 连接不上，请管理员先启动这些服务"
    else:
        reason = str(exc) if exc else "依赖服务不可用"
    return (
        f"【知识库暂不可用】检索失败：{reason}。\n"
        "请如实告诉用户知识库检索出错及原因，恢复后再提问。"
        "禁止用通用知识编造答案冒充知识库内容；"
        "如果用户明确需要通用建议，必须先注明「以下内容并非来自知识库」。"
    )
