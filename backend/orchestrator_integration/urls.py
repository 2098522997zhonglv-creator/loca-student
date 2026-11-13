"""URL路由配置"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OrchestratorTaskViewSet
from .agent_loop_view import (
    AgentLoopStreamAPIView,
    AgentLoopStopAPIView,
    AgentLoopResumeAPIView,
    AgentTaskSnapshotAPIView,
    AgentTaskConfigAPIView,
    AgentTaskConfigValidateAPIView,
    AgentTaskBudgetExpandAPIView,
    AgentEvidenceReadAPIView,
    AgentQualityMetricsAPIView,
)

router = DefaultRouter()
router.register(r'tasks', OrchestratorTaskViewSet, basename='orchestrator-task')

urlpatterns = [
    path('', include(router.urls)),
    # Agent Loop 流式对话接口 - 解决 Token 累积问题
    path('agent-loop/', AgentLoopStreamAPIView.as_view(), name='agent-loop-stream'),
    # Agent Loop 停止接口 - 中断正在执行的任务
    path('agent-loop/stop/', AgentLoopStopAPIView.as_view(), name='agent-loop-stop'),
    # Agent Loop 恢复接口 - HITL 审批后继续执行
    path('agent-loop/resume/', AgentLoopResumeAPIView.as_view(), name='agent-loop-resume'),
    # 任务快照 / 事件补取（FR10）
    path('agent-loop/task/', AgentTaskSnapshotAPIView.as_view(), name='agent-loop-task'),
    # 任务执行配置预检（D04）
    path(
        'agent-loop/config/validate/',
        AgentTaskConfigValidateAPIView.as_view(),
        name='agent-loop-config-validate',
    ),
    # RC12：任务配置 PATCH（乐观锁）
    path(
        'agent-loop/config/',
        AgentTaskConfigAPIView.as_view(),
        name='agent-loop-config',
    ),
    # RC15：预算扩容 / 能力扩权确认（分离）
    path(
        'agent-loop/config/expand/',
        AgentTaskBudgetExpandAPIView.as_view(),
        name='agent-loop-config-expand',
    ),
    # 证据授权分页读取（RC16）
    path(
        'agent-loop/evidence/',
        AgentEvidenceReadAPIView.as_view(),
        name='agent-loop-evidence',
    ),
    # 质量指标看板（FR10.4）
    path(
        'agent-loop/metrics/',
        AgentQualityMetricsAPIView.as_view(),
        name='agent-loop-metrics',
    ),
]
