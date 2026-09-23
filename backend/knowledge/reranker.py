"""Semantic reranker client for open-source / remote HTTP models.

Supported service styles:
- xinference: base URL + /v1/rerank (Xinference / many OpenAI-compatible gateways)
- openai_compatible: same protocol; URL may be base or full .../v1/rerank
- custom: alias of openai_compatible (backward compatible)
- tei: Hugging Face Text Embeddings Inference /rerank
- jina: Jina AI /v1/rerank
- cohere: Cohere-compatible /v1/rerank (results[].relevance_score or relevance_score)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "bge-reranker-v2-m3"
# 超时后短暂熔断，避免同一次对话里连续 2～3 次各卡满超时
_CIRCUIT_COOLDOWN_SEC = 60.0
_circuit_open_until = 0.0
SUPPORTED_SERVICES = (
    "none",
    "xinference",
    "openai_compatible",
    "custom",
    "tei",
    "jina",
    "cohere",
)


@dataclass
class RerankerEndpoint:
    service: str
    url: str
    model: str
    api_key: Optional[str] = None


def _normalize_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def resolve_reranker_endpoint(
    *,
    service: str,
    api_url: Optional[str],
    model_name: Optional[str],
    api_key: Optional[str] = None,
    fallback_base_url: Optional[str] = None,
) -> Optional[RerankerEndpoint]:
    """Resolve a concrete HTTP endpoint from global config fields."""
    service = (service or "none").strip().lower()
    if service in ("", "none"):
        return None
    if service not in SUPPORTED_SERVICES:
        logger.warning("Unsupported reranker service=%s", service)
        return None

    # custom kept as alias of openai_compatible
    style = "openai_compatible" if service == "custom" else service

    raw = _normalize_base(api_url or "")
    if not raw:
        if fallback_base_url:
            raw = _normalize_base(fallback_base_url)
        elif style == "xinference":
            raw = "http://localhost:9997"
        else:
            return None

    if style in ("xinference", "openai_compatible", "jina", "cohere"):
        if raw.endswith("/v1/rerank"):
            url = raw
        elif raw.endswith("/rerank"):
            # TEI-style path provided by mistake for openai-like service
            url = raw if style == "tei" else f"{raw.rsplit('/rerank', 1)[0]}/v1/rerank"
        else:
            url = f"{raw}/v1/rerank"
    elif style == "tei":
        if raw.endswith("/rerank") or raw.endswith("/v1/rerank"):
            url = raw
        else:
            url = f"{raw}/rerank"
    else:
        url = raw

    model = (model_name or "").strip() or DEFAULT_MODEL
    return RerankerEndpoint(
        service=style,
        url=url,
        model=model,
        api_key=(api_key or None),
    )


def _build_headers(api_key: Optional[str], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def _build_request_body(endpoint: RerankerEndpoint, query: str, documents: Sequence[str], top_n: int) -> Dict[str, Any]:
    style = endpoint.service
    if style == "tei":
        # HF TEI / Infinity TEI-compatible
        return {
            "query": query,
            "texts": list(documents),
            "raw_scores": False,
            "return_text": False,
        }
    if style == "cohere":
        return {
            "model": endpoint.model,
            "query": query,
            "documents": list(documents),
            "top_n": top_n,
            "return_documents": False,
        }
    if style == "jina":
        return {
            "model": endpoint.model,
            "query": query,
            "documents": list(documents),
            "top_n": top_n,
        }
    # xinference / openai_compatible / custom
    return {
        "model": endpoint.model,
        "query": query,
        "documents": list(documents),
        "top_n": top_n,
    }


def _extract_scored_indices(payload: Any, doc_count: int) -> List[Tuple[int, float]]:
    """Normalize heterogeneous rerank responses into [(index, score), ...]."""
    scored: List[Tuple[int, float]] = []

    if isinstance(payload, list):
        # TEI often returns [{index, score}, ...]
        for item in payload:
            if not isinstance(item, dict):
                continue
            if "index" in item and ("score" in item or "relevance_score" in item):
                idx = int(item["index"])
                score = float(item.get("relevance_score", item.get("score", 0.0)))
                scored.append((idx, score))
        return [(i, s) for i, s in scored if 0 <= i < doc_count]

    if not isinstance(payload, dict):
        return []

    results = payload.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            if "index" in item:
                idx = int(item["index"])
                score = float(
                    item.get("relevance_score", item.get("score", item.get("relevanceScore", 0.0)))
                )
                scored.append((idx, score))
            elif "document" in item and isinstance(item.get("index"), int):
                scored.append((int(item["index"]), float(item.get("relevance_score", 0.0))))
        if scored:
            return [(i, s) for i, s in scored if 0 <= i < doc_count]

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "index" in item:
                idx = int(item["index"])
                score = float(item.get("relevance_score", item.get("score", 0.0)))
                scored.append((idx, score))
        if scored:
            return [(i, s) for i, s in scored if 0 <= i < doc_count]

    # Some gateways return scores aligned by input order
    scores = payload.get("scores") or payload.get("relevance_scores")
    if isinstance(scores, list) and len(scores) == doc_count:
        return [(i, float(scores[i])) for i in range(doc_count)]

    return []


class SemanticReranker:
    """HTTP client that ranks documents with an external open-source rerank model."""

    def __init__(self, endpoint: RerankerEndpoint, timeout: float = 15.0):
        self.endpoint = endpoint
        # CPU 上 bge-reranker 偶发卡死；默认 15s 超时后回退稠密排序，避免单次问答卡满 120s×N
        self.timeout = timeout

    @classmethod
    def from_global_config(cls, config) -> Optional["SemanticReranker"]:
        endpoint = resolve_reranker_endpoint(
            service=getattr(config, "reranker_service", "none"),
            api_url=getattr(config, "reranker_api_url", None),
            model_name=getattr(config, "reranker_model_name", None),
            api_key=getattr(config, "reranker_api_key", None) or None,
            fallback_base_url=(
                getattr(config, "api_base_url", None)
                if getattr(config, "embedding_service", None) == "xinference"
                else None
            ),
        )
        if not endpoint:
            return None
        return cls(endpoint)

    def rerank_texts(self, query: str, documents: Sequence[str], top_n: int) -> List[Tuple[int, float]]:
        global _circuit_open_until
        if not documents:
            return []
        now = time.monotonic()
        if now < _circuit_open_until:
            raise RuntimeError(
                f"Reranker circuit open until +{_circuit_open_until - now:.0f}s "
                "(previous timeout); skip to fallback ranking"
            )
        top_n = max(1, min(int(top_n or len(documents)), len(documents)))
        body = _build_request_body(self.endpoint, query, documents, top_n)
        headers = _build_headers(self.endpoint.api_key)

        session = requests.Session()
        session.trust_env = False
        logger.info(
            "RERANK request service=%s url=%s model=%s docs=%s top_n=%s timeout=%.0fs",
            self.endpoint.service,
            self.endpoint.url,
            self.endpoint.model,
            len(documents),
            top_n,
            self.timeout,
        )
        try:
            response = session.post(
                self.endpoint.url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SEC
            logger.warning(
                "RERANK timeout/connect fail, circuit open %ss: %s",
                _CIRCUIT_COOLDOWN_SEC,
                exc,
            )
            raise RuntimeError(str(exc)) from exc
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

        payload = response.json()
        scored = _extract_scored_indices(payload, len(documents))
        if not scored:
            raise RuntimeError(f"Unrecognized rerank response: {str(payload)[:300]}")

        _circuit_open_until = 0.0
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_n]

    def test_connection(self) -> Dict[str, Any]:
        query = "What is machine learning?"
        documents = [
            "Machine learning is a subset of artificial intelligence.",
            "The weather is nice today.",
        ]
        scored = self.rerank_texts(query, documents, top_n=2)
        return {
            "success": True,
            "message": "Reranker 服务测试成功！服务运行正常",
            "sample_scores": [{"index": i, "score": s} for i, s in scored],
            "endpoint": self.endpoint.url,
            "service": self.endpoint.service,
            "model": self.endpoint.model,
        }
