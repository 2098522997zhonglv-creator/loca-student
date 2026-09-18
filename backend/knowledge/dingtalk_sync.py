"""钉钉知识库同步：增量对齐本地 Document。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Set

from django.utils import timezone

from .dingtalk_client import (
    DingTalkAPIError,
    DingTalkClient,
    parse_dingtalk_modified_time,
)
from .models import DingTalkSyncBinding, Document
from .services import KnowledgeBaseService

logger = logging.getLogger(__name__)


def archive_document(document: Document) -> None:
    """软归档：删向量并标记 is_archived。"""
    if document.is_archived:
        return
    try:
        service = KnowledgeBaseService(document.knowledge_base)
        service.vector_manager.delete_document(document)
    except Exception as e:
        logger.warning("归档时删除向量失败 doc=%s: %s", document.id, e)
    document.chunks.all().delete()
    document.is_archived = True
    document.save(update_fields=["is_archived"])


def upsert_dingtalk_document(
    *,
    knowledge_base,
    node: Dict[str, Any],
    uploader=None,
    path: str = "",
    process: bool = True,
) -> tuple[Document, str]:
    """
    按 external_id 创建或更新钉钉文档。
    返回 (document, action) action in created|updated|skipped|failed
    """
    node_id = node.get("nodeId")
    if not node_id:
        return None, "failed"  # type: ignore

    title = (node.get("name") or node_id)[:200]
    url = node.get("url") or ""
    workspace_id = node.get("workspaceId") or ""
    modified_at = parse_dingtalk_modified_time(node)

    existing = Document.objects.filter(
        knowledge_base=knowledge_base,
        document_type="dingtalk",
        external_id=node_id,
    ).first()

    if existing:
        changed = False
        if existing.is_archived:
            existing.is_archived = False
            changed = True
        if title and existing.title != title:
            existing.title = title
            changed = True
        if url and existing.url != url:
            existing.url = url
            existing.external_url = url
            changed = True
        if path and existing.external_path != path:
            existing.external_path = path[:500]
            changed = True
        need_reprocess = False
        if modified_at and (
            not existing.external_modified_at
            or modified_at > existing.external_modified_at
        ):
            existing.external_modified_at = modified_at
            need_reprocess = True
            changed = True
        elif not existing.external_modified_at and modified_at:
            existing.external_modified_at = modified_at
            changed = True

        if changed:
            existing.save()
        if need_reprocess or existing.status == "failed":
            existing.status = "pending"
            existing.error_message = ""
            existing.save(update_fields=["status", "error_message"])
            if process:
                _process_now(existing)
            return existing, "updated"
        return existing, "skipped"

    doc = Document.objects.create(
        knowledge_base=knowledge_base,
        title=title,
        document_type="dingtalk",
        url=url or None,
        external_id=node_id,
        external_workspace_id=workspace_id or None,
        external_url=url or None,
        external_path=(path[:500] if path else None),
        external_modified_at=modified_at,
        uploader=uploader,
        status="pending",
    )
    if process:
        _process_now(doc)
    return doc, "created"


def _process_now(document: Document) -> None:
    service = KnowledgeBaseService(document.knowledge_base)
    service.process_document(document)


def sync_binding(binding_id: str, uploader=None) -> Dict[str, Any]:
    """执行一次绑定同步，返回报告字典。"""
    binding = DingTalkSyncBinding.objects.select_related("knowledge_base").get(
        id=binding_id
    )
    report: Dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "archived": 0,
        "ignored": 0,
        "errors": [],
    }

    binding.last_status = "running"
    binding.last_error = ""
    binding.save(update_fields=["last_status", "last_error", "updated_at"])

    try:
        client = DingTalkClient()
        client.ensure_enabled()

        root_node_id = (binding.root_node_id or "").strip()
        if not root_node_id:
            ws = client.get_workspace(binding.workspace_id)
            root_node_id = ws.get("rootNodeId") or ws.get("root_node_id") or ""
            if not binding.workspace_name:
                binding.workspace_name = (
                    ws.get("name") or ws.get("workspaceName") or ""
                )[:200]
            if not root_node_id:
                raise DingTalkAPIError(
                    f"知识库 {binding.workspace_id} 缺少 rootNodeId，请在绑定中填写 root_node_id"
                )

        seen_ids: Set[str] = set()
        for node in client.walk_nodes(root_node_id):
            node_type = (node.get("type") or "").upper()
            category = (node.get("category") or "").upper()
            node_id = node.get("nodeId")
            if not node_id:
                continue
            if node_type == "FOLDER":
                continue
            if category and category != "ALIDOC":
                report["ignored"] += 1
                continue
            if node_type == "FILE" and category and category != "ALIDOC":
                report["ignored"] += 1
                continue
            # 无 category 时：FILE 也尝试当 ALIDOC（部分接口缺字段）
            if node_type == "FILE" and not category:
                pass
            elif node_type and node_type != "FILE":
                report["ignored"] += 1
                continue

            seen_ids.add(node_id)
            try:
                _, action = upsert_dingtalk_document(
                    knowledge_base=binding.knowledge_base,
                    node=node,
                    uploader=uploader,
                    path=node.get("_path") or "",
                    process=True,
                )
                if action in report:
                    report[action] += 1
                else:
                    report["failed"] += 1
            except Exception as e:
                report["failed"] += 1
                msg = f"{node.get('name') or node_id}: {e}"
                report["errors"].append(msg[:300])
                logger.exception("同步节点失败: %s", msg)

        # 软归档：本绑定 workspace 下本地有、远端没有
        local_qs = Document.objects.filter(
            knowledge_base=binding.knowledge_base,
            document_type="dingtalk",
            is_archived=False,
        )
        if binding.workspace_id:
            local_qs = local_qs.filter(external_workspace_id=binding.workspace_id)
        for doc in local_qs:
            if doc.external_id and doc.external_id not in seen_ids:
                try:
                    archive_document(doc)
                    report["archived"] += 1
                except Exception as e:
                    report["failed"] += 1
                    report["errors"].append(f"归档 {doc.title}: {e}"[:300])

        binding.last_status = "success"
        binding.last_synced_at = timezone.now()
        binding.last_error = ""
        binding.last_report = report
        binding.save(
            update_fields=[
                "last_status",
                "last_synced_at",
                "last_error",
                "last_report",
                "workspace_name",
                "updated_at",
            ]
        )
        return report

    except Exception as e:
        logger.exception("钉钉同步失败 binding=%s", binding_id)
        binding.last_status = "failed"
        binding.last_error = str(e)[:1000]
        binding.last_report = report
        binding.last_synced_at = timezone.now()
        binding.save(
            update_fields=[
                "last_status",
                "last_error",
                "last_report",
                "last_synced_at",
                "updated_at",
            ]
        )
        raise


def import_from_url(knowledge_base, url: str, title: str = "", uploader=None) -> Document:
    """贴链接导入：解析节点后 upsert 并处理。"""
    client = DingTalkClient()
    client.ensure_enabled()
    node = client.get_node_by_url(url)
    if title:
        node = dict(node)
        node["name"] = title
    if not node.get("url"):
        node = dict(node)
        node["url"] = url
    doc, action = upsert_dingtalk_document(
        knowledge_base=knowledge_base,
        node=node,
        uploader=uploader,
        path="",
        process=True,
    )
    if doc is None:
        raise DingTalkAPIError("无法从链接创建文档")
    logger.info("钉钉贴链接导入 kb=%s action=%s doc=%s", knowledge_base.id, action, doc.id)
    return doc
