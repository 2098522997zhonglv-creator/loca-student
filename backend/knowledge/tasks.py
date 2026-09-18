"""知识库异步任务"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='knowledge.process_document')
def process_document_task(self, document_id):
    """异步处理文档：加载、分块、向量化"""
    from .models import Document
    from .services import KnowledgeBaseService

    try:
        document = Document.objects.select_related('knowledge_base').get(id=document_id)
        logger.info(
            "KB_DOC process start doc_id=%s kb_id=%s title=%r",
            document_id,
            document.knowledge_base_id,
            (document.title or "")[:80],
        )
        service = KnowledgeBaseService(document.knowledge_base)
        service.process_document(document)
        logger.info(
            "KB_DOC process done doc_id=%s kb_id=%s status=%s chunks=%s",
            document_id,
            document.knowledge_base_id,
            document.status,
            document.chunks.count() if hasattr(document, "chunks") else "n/a",
        )
    except Document.DoesNotExist:
        logger.error("KB_DOC process missing doc_id=%s", document_id)
    except Exception as e:
        logger.error("KB_DOC process failed doc_id=%s error=%s", document_id, e, exc_info=True)
        try:
            Document.objects.filter(id=document_id).update(
                status='failed', error_message=str(e)[:500]
            )
        except Exception:
            pass
        raise


@shared_task(bind=True, name='knowledge.sync_dingtalk_binding')
def sync_dingtalk_binding_task(self, binding_id, user_id=None):
    """同步单个钉钉绑定。"""
    from django.contrib.auth.models import User

    from .dingtalk_sync import sync_binding

    uploader = None
    if user_id:
        uploader = User.objects.filter(id=user_id).first()
    logger.info("DINGTALK sync start binding_id=%s user_id=%s", binding_id, user_id)
    report = sync_binding(binding_id, uploader=uploader)
    logger.info("DINGTALK sync done binding_id=%s report=%s", binding_id, report)
    return report


@shared_task(name='knowledge.sync_due_dingtalk_bindings')
def sync_due_dingtalk_bindings():
    """扫描到期的钉钉绑定并派发同步任务。"""
    from .models import DingTalkConfig, DingTalkSyncBinding

    cfg = DingTalkConfig.get_config()
    if not cfg.enabled:
        logger.info("DINGTALK tick skipped: config disabled")
        return {"dispatched": 0, "reason": "disabled"}

    now = timezone.now()
    dispatched = 0
    for binding in DingTalkSyncBinding.objects.filter(enabled=True).select_related(
        "knowledge_base"
    ):
        if binding.last_status == "running":
            # 避免重叠；若卡死超过 2 小时则允许重试
            if binding.updated_at and now - binding.updated_at < timedelta(hours=2):
                continue
        interval = max(int(binding.interval_minutes or 60), 15)
        if binding.last_synced_at and now - binding.last_synced_at < timedelta(
            minutes=interval
        ):
            continue
        sync_dingtalk_binding_task.delay(str(binding.id))
        dispatched += 1

    logger.info("DINGTALK tick dispatched=%s", dispatched)
    return {"dispatched": dispatched}
