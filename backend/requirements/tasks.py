"""
需求评审异步任务
"""
import logging
import threading
import uuid

from celery import shared_task
from django.conf import settings
from django.db import connections
from django.utils import timezone

logger = logging.getLogger(__name__)


def dispatch_requirement_review(
    document_id, analysis_options=None, review_type="comprehensive", user_id=None
):
    """派发评审任务，返回任务标识。

    优先交给独立的 Celery worker，评审才能脱离 Web 进程的 GIL 竞争。
    broker 不可用或显式配置了 eager 模式时，退回后台线程执行：功能仍然可用，
    但评审会和请求处理抢同一个进程的资源。
    """
    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            return execute_requirement_review.delay(
                document_id, analysis_options, review_type, user_id=user_id
            ).id
        except Exception:
            logger.warning(
                "REQ_REVIEW celery broker unavailable (%s), falling back to a "
                "background thread; review will compete with web requests",
                getattr(settings, "CELERY_BROKER_URL", "?"),
                exc_info=True,
            )

    return _dispatch_in_thread(document_id, analysis_options, review_type, user_id)


def _dispatch_in_thread(document_id, analysis_options, review_type, user_id):
    """在后台线程执行评审，让 HTTP 请求能立即返回。"""
    task_id = str(uuid.uuid4())

    def _run():
        try:
            execute_requirement_review(
                document_id, analysis_options, review_type, user_id=user_id
            )
        except Exception:
            logger.error("REQ_REVIEW background thread crashed", exc_info=True)
        finally:
            # 线程结束时释放数据库连接，避免连接泄漏
            connections.close_all()

    threading.Thread(
        target=_run, name=f"requirement-review-{document_id}", daemon=True
    ).start()
    return task_id


@shared_task(bind=True, name='requirements.execute_requirement_review')
def execute_requirement_review(self, document_id, analysis_options=None, review_type='comprehensive', user_id=None):
    """
    异步执行需求评审任务
    
    Args:
        document_id: 文档ID
        analysis_options: 分析选项
        review_type: 评审类型 ('direct' 或 'comprehensive')
        user_id: 用户ID（用于获取用户的提示词配置）
    """
    from .models import RequirementDocument, ReviewReport
    from .services import RequirementReviewService
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    
    try:
        logger.info(
            "REQ_REVIEW task start document_id=%s review_type=%s user_id=%s",
            document_id,
            review_type,
            user_id,
        )
        
        # 获取文档
        document = RequirementDocument.objects.get(id=document_id)
        
        # 获取用户对象
        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
                logger.info(f"开始异步评审文档: {document.title}, 类型: {review_type}, 用户: {user.username}")
            except User.DoesNotExist:
                logger.warning(f"用户 {user_id} 不存在，使用默认配置")
        
        # 文档状态应该已经在视图中设置为 reviewing 了，这里不需要重复设置
        
        # 创建评审服务，传入用户对象
        review_service = RequirementReviewService(user=user)
        
        # 根据类型执行评审
        if review_type == 'direct':
            review_report = review_service.start_direct_review(
                document,
                analysis_options or {}
            )
        else:
            review_report = review_service.start_comprehensive_review(
                document,
                analysis_options or {}  # 传递完整的analysis_options
            )
        
        logger.info(
            "REQ_REVIEW done document_id=%s report_id=%s score=%s issues=%s",
            document_id,
            review_report.id,
            getattr(review_report, "completion_score", None),
            getattr(review_report, "total_issues", None),
        )
        
        return {
            'status': 'success',
            'document_id': str(document_id),
            'report_id': str(review_report.id),
            'completion_score': review_report.completion_score,
            'total_issues': review_report.total_issues
        }
        
    except RequirementDocument.DoesNotExist:
        logger.error(f"文档不存在: {document_id}")
        return {
            'status': 'error',
            'message': f'文档不存在: {document_id}'
        }
    except Exception as e:
        logger.error(f"评审任务失败: {e}", exc_info=True)
        
        # 更新文档状态为失败
        try:
            document = RequirementDocument.objects.get(id=document_id)
            document.status = 'failed'
            document.save()

            # 同步把仍在进行中的报告标记为失败，否则前端会一直停在旧进度
            document.review_reports.filter(status='in_progress').update(
                status='failed',
                current_step='评审失败，请重试',
                updated_at=timezone.now(),
            )
        except Exception:
            logger.warning("REQ_REVIEW failed to mark document/report as failed", exc_info=True)
        
        return {
            'status': 'error',
            'message': str(e)
        }