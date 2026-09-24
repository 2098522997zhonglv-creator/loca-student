"""账号行为本地向量记忆：记录工具/纠正/HITL，对话前 prefetch 注入。"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = "dense"
COLLECTION_PREFIX = "user_behavior_"

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(--header\s+)([\"']?)([^\"'\s]+)\2"), r"\1\2[REDACTED]\2"),
    (re.compile(r"(?i)(Authorization:\s*Bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Cookie:\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(LOCA_STUDE_API_KEY\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(session=)\S+"), r"\1[REDACTED]"),
]

_CORRECTION_HINTS = (
    "不对",
    "不是",
    "错了",
    "应该是",
    "改成",
    "纠正",
    "更正",
    "实际是",
    "正确答案",
    "搞错了",
)


def sanitize_behavior_text(text: str, max_len: int = 800) -> str:
    """脱敏并截断，避免把 Cookie/密钥写入行为库。"""
    if not text:
        return ""
    out = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out


def looks_like_user_correction(message: str) -> bool:
    text = (message or "").strip()
    if len(text) < 2:
        return False
    return any(h in text for h in _CORRECTION_HINTS)


def collection_name_for_user(user_id) -> str:
    return f"{COLLECTION_PREFIX}{user_id}"


def _get_embeddings():
    """复用 KnowledgeGlobalConfig + VectorStoreManager 的嵌入构造逻辑。"""
    from knowledge.services import VectorStoreManager

    dummy = VectorStoreManager.__new__(VectorStoreManager)
    dummy.global_config = VectorStoreManager._get_global_config()
    return dummy._get_embeddings_instance()


def _get_qdrant_client():
    from knowledge.services import VectorStoreManager

    return VectorStoreManager._new_qdrant_client()


def _ensure_collection(client, collection: str, vector_size: int) -> None:
    from qdrant_client.models import Distance, VectorParams

    try:
        exists = bool(client.collection_exists(collection))
    except Exception:
        try:
            client.get_collection(collection)
            exists = True
        except Exception:
            exists = False

    if exists:
        return

    client.create_collection(
        collection_name=collection,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(size=vector_size, distance=Distance.COSINE)
        },
    )
    logger.info("创建账号行为集合: %s dim=%s", collection, vector_size)


class UserBehaviorStore:
    """账号行为：SQLite/ORM 事件表 + Qdrant dense 向量。"""

    def record(
        self,
        user_id: int,
        event_type: str,
        summary_text: str,
        *,
        project_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        from .models import UserBehaviorEvent

        summary = sanitize_behavior_text(summary_text)
        if not summary or not user_id:
            return None

        meta = dict(metadata or {})
        vector_id = str(uuid.uuid4())

        try:
            event = UserBehaviorEvent.objects.create(
                user_id=user_id,
                project_id=project_id,
                event_type=event_type,
                summary_text=summary,
                metadata=meta,
                vector_id=vector_id,
            )
        except Exception as e:
            logger.warning("写入 UserBehaviorEvent 失败: %s", e)
            return None

        try:
            embeddings = _get_embeddings()
            vector = embeddings.embed_query(summary)
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
                            "event_type": event_type,
                            "summary_text": summary,
                            "project_id": project_id,
                            "user_id": user_id,
                            "metadata": meta,
                        },
                    )
                ],
            )
            logger.info(
                "账号行为已向量化 user=%s type=%s id=%s",
                user_id,
                event_type,
                vector_id,
            )
            return vector_id
        except Exception as e:
            logger.warning(
                "账号行为向量写入失败（事件已落库） user=%s: %s", user_id, e
            )
            return vector_id

    def search(
        self, user_id: int, query: str, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not user_id or not query:
            return []

        collection = collection_name_for_user(user_id)
        try:
            client = _get_qdrant_client()
            try:
                if not client.collection_exists(collection):
                    return []
            except Exception:
                try:
                    client.get_collection(collection)
                except Exception:
                    return []

            embeddings = _get_embeddings()
            vector = embeddings.embed_query(query)
            from qdrant_client.models import NamedVector

            hits = client.search(
                collection_name=collection,
                query_vector=NamedVector(name=DENSE_VECTOR_NAME, vector=vector),
                limit=max(1, min(int(top_k or 5), 10)),
                with_payload=True,
            )
            results = []
            for hit in hits:
                payload = hit.payload or {}
                results.append(
                    {
                        "id": str(hit.id),
                        "score": float(hit.score or 0.0),
                        "event_type": payload.get("event_type"),
                        "summary_text": payload.get("summary_text") or "",
                        "metadata": payload.get("metadata") or {},
                    }
                )
            return results
        except Exception as e:
            logger.warning("账号行为检索失败 user=%s: %s", user_id, e)
            return []

    def drop_collection(self, user_id: int) -> None:
        collection = collection_name_for_user(user_id)
        try:
            client = _get_qdrant_client()
            if client.collection_exists(collection):
                client.delete_collection(collection)
                logger.info("已删除账号行为集合: %s", collection)
        except Exception as e:
            logger.warning("删除账号行为集合失败 %s: %s", collection, e)


def prefetch_user_behavior_context(
    user_id: int, query: str, top_k: int = 5
) -> str:
    """对话前注入的账号偏好摘要；失败返回空串。"""
    if not user_id or not (query or "").strip():
        return ""
    try:
        store = UserBehaviorStore()
        hits = store.search(user_id, query, top_k=top_k)
        if not hits:
            return ""
        lines = ["# 账号行为偏好（本地向量）", "以下为与本轮问题相关的历史习惯/纠正，事实仍以知识库为准："]
        for i, hit in enumerate(hits, 1):
            et = hit.get("event_type") or "event"
            score = hit.get("score")
            score_s = f"{score:.3f}" if isinstance(score, float) else "-"
            summary = sanitize_behavior_text(hit.get("summary_text") or "", max_len=240)
            lines.append(f"{i}. [{et} score={score_s}] {summary}")
        logger.info(
            "账号行为预检索命中 user=%s hits=%s", user_id, len(hits)
        )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("账号行为预检索异常 user=%s: %s", user_id, e)
        return ""


def record_tool_call_behavior(
    user_id: int,
    tool_name: str,
    tool_output: str = "",
    *,
    project_id: Optional[int] = None,
    skill_name: Optional[str] = None,
    command: Optional[str] = None,
) -> None:
    ok = not str(tool_output or "").lstrip().startswith("错误")
    cmd = sanitize_behavior_text(command or "", max_len=300)
    skill = skill_name or ""
    summary = (
        f"工具调用 tool={tool_name}"
        + (f" skill={skill}" if skill else "")
        + (f" cmd={cmd}" if cmd else "")
        + f" result={'ok' if ok else 'fail'}"
    )
    UserBehaviorStore().record(
        user_id,
        "tool_call",
        summary,
        project_id=project_id,
        metadata={
            "tool_name": tool_name,
            "skill_name": skill,
            "ok": ok,
        },
    )


def record_user_correction_behavior(
    user_id: int,
    message: str,
    *,
    project_id: Optional[int] = None,
) -> None:
    if not looks_like_user_correction(message):
        return
    UserBehaviorStore().record(
        user_id,
        "user_correction",
        f"用户纠正: {sanitize_behavior_text(message, max_len=400)}",
        project_id=project_id,
        metadata={},
    )


def record_hitl_decision_behavior(
    user_id: int,
    decision_type: str,
    *,
    project_id: Optional[int] = None,
    tool_names: Optional[List[str]] = None,
) -> None:
    tools = ", ".join(tool_names or []) or "unknown"
    UserBehaviorStore().record(
        user_id,
        "hitl_decision",
        f"HITL 决策 type={decision_type} tools={tools}",
        project_id=project_id,
        metadata={"decision_type": decision_type, "tools": tool_names or []},
    )


def extract_skill_meta_from_tool_message(tool_name: str, content: str) -> Dict[str, str]:
    """从工具输出中尽力解析 skill_name（弱匹配，失败则空）。"""
    meta: Dict[str, str] = {}
    text = content or ""
    m = re.search(r"skill_name[=:]\s*([a-zA-Z0-9_-]+)", text)
    if m:
        meta["skill_name"] = m.group(1)
    if tool_name in ("execute_skill_script", "read_skill_content"):
        # 常见回显格式
        m2 = re.search(r"Skill['\"]?\s*[:=]\s*['\"]?([a-zA-Z0-9_-]+)", text)
        if m2 and "skill_name" not in meta:
            meta["skill_name"] = m2.group(1)
    return meta
