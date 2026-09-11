"""知识库异步任务"""
import logging
from celery import shared_task

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
