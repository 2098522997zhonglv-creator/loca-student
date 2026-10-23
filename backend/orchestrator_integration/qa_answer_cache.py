"""同用户同知识库的问答答案缓存：向量命中复用，纠错时失效。"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from orchestrator_integration.behavior_memory import (
    DENSE_VECTOR_NAME,
    _ensure_collection,
    _get_embeddings,
    _get_qdrant_client,
    collection_name_for_user,
    looks_like_user_correction,
    sanitize_behavior_text,
)

logger = logging.getLogger(__name__)

EVENT_TYPE = "kb_qa_answer"
# cosine 相似度阈值：同问/近义问才复用
HIT_THRESHOLD = 0.82
# 纠错时放宽一点，尽量清掉相关旧答
INVALIDATE_THRESHOLD = 0.72
ANSWER_MAX_LEN = 1200
QUESTION_MAX_LEN = 400


def _kb_id_int(knowledge_base_id) -> Optional[int]:
    try:
        return int(knowledge_base_id)
    except (TypeError, ValueError):
        return None


def _qa_filter(knowledge_base_id: int, *, active_only: bool = True):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    must = [
        FieldCondition(key="event_type", match=MatchValue(value=EVENT_TYPE)),
        FieldCondition(
            key="knowledge_base_id", match=MatchValue(value=int(knowledge_base_id))
        ),
    ]
    if active_only:
        must.append(FieldCondition(key="active", match=MatchValue(value=True)))
    return Filter(must=must)


def lookup_kb_qa_answer(
    user_id: int,
    knowledge_base_id,
    question: str,
    *,
    threshold: float = HIT_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """按问句向量检索同用户同知识库的有效历史答案。"""
    kb_id = _kb_id_int(knowledge_base_id)
    q = sanitize_behavior_text(question or "", max_len=QUESTION_MAX_LEN)
    if not user_id or not kb_id or len(q) < 2:
        return None

    collection = collection_name_for_user(user_id)
    try:
        client = _get_qdrant_client()
        try:
            if not client.collection_exists(collection):
                return None
        except Exception:
            try:
                client.get_collection(collection)
            except Exception:
                return None

        embeddings = _get_embeddings()
        vector = embeddings.embed_query(q)
        from qdrant_client.models import NamedVector

        hits = client.search(
            collection_name=collection,
            query_vector=NamedVector(name=DENSE_VECTOR_NAME, vector=vector),
            query_filter=_qa_filter(kb_id, active_only=True),
            limit=3,
            with_payload=True,
        )
        if not hits:
            return None
        best = hits[0]
        score = float(best.score or 0.0)
        if score < float(threshold):
            logger.info(
                "QA 缓存未达阈值 user=%s kb=%s score=%.3f thr=%.3f",
                user_id,
                kb_id,
                score,
                threshold,
            )
            return None
        payload = best.payload or {}
        answer = sanitize_behavior_text(
            payload.get("answer") or "", max_len=ANSWER_MAX_LEN
        )
        if not answer:
            return None
        result = {
            "id": str(best.id),
            "score": score,
            "question": payload.get("summary_text") or q,
            "answer": answer,
            "sources": payload.get("sources") or [],
            "knowledge_base_id": kb_id,
        }
        logger.info(
            "QA 缓存命中 user=%s kb=%s score=%.3f id=%s",
            user_id,
            kb_id,
            score,
            result["id"],
        )
        return result
    except Exception as e:
        logger.warning("QA 缓存检索失败 user=%s kb=%s: %s", user_id, knowledge_base_id, e)
        return None


def format_cached_answer_context(hit: Dict[str, Any]) -> str:
    """注入 system prompt 的历史答案块。"""
    if not hit:
        return ""
    answer = hit.get("answer") or ""
    sources = hit.get("sources") or []
    score = hit.get("score")
    score_s = f"{score:.3f}" if isinstance(score, float) else "-"
    src_line = ""
    if sources:
        names = "、".join(str(s) for s in sources[:5] if s)
        if names:
            src_line = f"\n- 历史来源文档: {names}"
    return (
        "## 历史已答（同用户同知识库缓存）\n"
        f"- 相似度: {score_s}\n"
        f"- 请优先沿用下方结论回答，并保留来源标注；不要再调 knowledge_search，"
        f"除非用户明确要求换答案/再查库。{src_line}\n\n"
        f"{answer}"
    )


def record_kb_qa_answer(
    user_id: int,
    knowledge_base_id,
    question: str,
    answer: str,
    *,
    project_id: Optional[int] = None,
    sources: Optional[List[str]] = None,
) -> Optional[str]:
    """写入一条有效问答缓存（向量按问句嵌入）。"""
    kb_id = _kb_id_int(knowledge_base_id)
    q = sanitize_behavior_text(question or "", max_len=QUESTION_MAX_LEN)
    a = sanitize_behavior_text(answer or "", max_len=ANSWER_MAX_LEN)
    if not user_id or not kb_id or len(q) < 2 or len(a) < 20:
        return None

    # 过短/寒暄式回答不入库
    if len(a) < 40 and "来源" not in a and "《" not in a:
        return None

    from .models import UserBehaviorEvent

    vector_id = str(uuid.uuid4())
    srcs = [str(s) for s in (sources or []) if s][:8]
    meta = {
        "knowledge_base_id": kb_id,
        "active": True,
        "sources": srcs,
        "answer_len": len(a),
    }

    try:
        event = UserBehaviorEvent.objects.create(
            user_id=user_id,
            project_id=project_id,
            event_type=EVENT_TYPE,
            summary_text=q,
            metadata={**meta, "answer": a[:500]},
            vector_id=vector_id,
        )
    except Exception as e:
        logger.warning("写入 QA 缓存事件失败: %s", e)
        return None

    try:
        embeddings = _get_embeddings()
        vector = embeddings.embed_query(q)
        client = _get_qdrant_client()
        collection = collection_name_for_user(user_id)
        _ensure_collection(client, collection, len(vector))

        from qdrant_client.models import PointStruct

        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=vector_id,
                    vector={DENSE_VECTOR_NAME: vector},
                    payload={
                        "event_id": event.id,
                        "event_type": EVENT_TYPE,
                        "summary_text": q,
                        "answer": a,
                        "sources": srcs,
                        "knowledge_base_id": kb_id,
                        "active": True,
                        "project_id": project_id,
                        "user_id": user_id,
                        "metadata": meta,
                    },
                )
            ],
        )
        logger.info(
            "QA 缓存已写入 user=%s kb=%s id=%s", user_id, kb_id, vector_id
        )
        return vector_id
    except Exception as e:
        logger.warning("QA 缓存向量写入失败（事件已落库） user=%s: %s", user_id, e)
        return vector_id


def invalidate_kb_qa_answers(
    user_id: int,
    knowledge_base_id,
    query: str,
    *,
    threshold: float = INVALIDATE_THRESHOLD,
) -> int:
    """纠错时：将同库相近问句的缓存标为失效。返回失效条数。"""
    kb_id = _kb_id_int(knowledge_base_id)
    q = sanitize_behavior_text(query or "", max_len=QUESTION_MAX_LEN)
    if not user_id or not kb_id or len(q) < 2:
        return 0

    collection = collection_name_for_user(user_id)
    try:
        client = _get_qdrant_client()
        try:
            if not client.collection_exists(collection):
                return 0
        except Exception:
            return 0

        embeddings = _get_embeddings()
        vector = embeddings.embed_query(q)
        from qdrant_client.models import NamedVector, PointStruct

        hits = client.search(
            collection_name=collection,
            query_vector=NamedVector(name=DENSE_VECTOR_NAME, vector=vector),
            query_filter=_qa_filter(kb_id, active_only=True),
            limit=8,
            with_payload=True,
        )
        inactivated = 0
        for hit in hits:
            score = float(hit.score or 0.0)
            if score < float(threshold):
                continue
            payload = dict(hit.payload or {})
            payload["active"] = False
            meta = dict(payload.get("metadata") or {})
            meta["active"] = False
            payload["metadata"] = meta
            # 保留原向量：先 retrieve 再 upsert
            points = client.retrieve(
                collection_name=collection,
                ids=[hit.id],
                with_vectors=True,
            )
            if not points:
                continue
            pt = points[0]
            vec = pt.vector
            client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=hit.id,
                        vector=vec if isinstance(vec, dict) else {DENSE_VECTOR_NAME: vec},
                        payload=payload,
                    )
                ],
            )
            event_id = payload.get("event_id")
            if event_id:
                try:
                    from .models import UserBehaviorEvent

                    ev = UserBehaviorEvent.objects.filter(id=event_id).first()
                    if ev:
                        meta = dict(ev.metadata or {})
                        meta["active"] = False
                        ev.metadata = meta
                        ev.save(update_fields=["metadata"])
                except Exception:
                    pass
            inactivated += 1

        if inactivated:
            logger.info(
                "QA 缓存纠错失效 user=%s kb=%s count=%s",
                user_id,
                kb_id,
                inactivated,
            )
        return inactivated
    except Exception as e:
        logger.warning("QA 缓存失效失败 user=%s: %s", user_id, e)
        return 0


def should_invalidate_for_message(message: str) -> bool:
    return looks_like_user_correction(message)


def extract_sources_from_answer(answer: str) -> List[str]:
    """从回答文本弱提取《文档名》来源。"""
    import re

    if not answer:
        return []
    return list(dict.fromkeys(re.findall(r"《([^》]{1,80})》", answer)))[:8]
