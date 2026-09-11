from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OperationLogViewSet,
    OperationLogCleanupAPIView,
    OperationLogSettingAPIView,
    RuntimeLogTailAPIView,
    RuntimeLogFilesAPIView,
    RuntimeLogDownloadAPIView,
)

router = DefaultRouter()
router.register(r'', OperationLogViewSet, basename='operation-log')

urlpatterns = [
    path('settings/', OperationLogSettingAPIView.as_view(), name='operation-log-settings'),
    path('cleanup-now/', OperationLogCleanupAPIView.as_view(), name='operation-log-cleanup-now'),
    path('runtime/', RuntimeLogTailAPIView.as_view(), name='runtime-log-tail'),
    path('runtime/files/', RuntimeLogFilesAPIView.as_view(), name='runtime-log-files'),
    path('runtime/download/', RuntimeLogDownloadAPIView.as_view(), name='runtime-log-download'),
    path('', include(router.urls)),
]
