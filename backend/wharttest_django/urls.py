"""Routes retained by the standalone knowledge center."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_nested.routers import NestedSimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import MyTokenObtainPairView
from file_management.views import FileAssetViewSet
from projects.views import ProjectViewSet
from skills.views import SkillViewSet
from testcases.views import (
    TestCaseViewSet,
    TestCaseModuleViewSet,
    TestSuiteViewSet,
    TestExecutionViewSet,
)

router = DefaultRouter()
router.register(r"projects", ProjectViewSet, basename="project")
project_router = NestedSimpleRouter(router, r"projects", lookup="project")
project_router.register(r"testcases", TestCaseViewSet, basename="project-testcases")
project_router.register(
    r"testcase-modules", TestCaseModuleViewSet, basename="project-testcase-modules"
)
project_router.register(r"test-suites", TestSuiteViewSet, basename="project-test-suites")
project_router.register(
    r"test-executions", TestExecutionViewSet, basename="project-test-executions"
)
project_router.register(r"skills", SkillViewSet, basename="project-skills")
project_router.register(r"files", FileAssetViewSet, basename="project-files")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/token/", MyTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/accounts/", include("accounts.urls")),
    path("api/", include(router.urls)),
    path("api/", include(project_router.urls)),
    path("api/", include("api_keys.urls")),
    path("api/", include("testcase_templates.urls")),
    path("api/ui-automation/", include("ui_automation.urls")),
    path("api/knowledge/", include("knowledge.urls")),
    path("api/requirements/", include("requirements.urls")),
    path("api/prompts/", include("prompts.urls")),
    path("api/lg/", include("langgraph_integration.urls")),
    path("api/orchestrator/", include("orchestrator_integration.urls")),
    path("api/mcp_tools/", include("mcp_tools.urls")),
    path("api/operation-logs/", include("operation_logs.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# After `npm run build`, Django's local server can serve the SPA as one process.
if (settings.FRONTEND_DIST / "index.html").exists():
    from django.views.generic import TemplateView
    from django.views.static import serve

    urlpatterns += [
        re_path(r"^assets/(?P<path>.*)$", serve, {"document_root": settings.FRONTEND_DIST / "assets"}),
        # Vite copies public/* to dist root (e.g. brand-mark.svg); serve before SPA fallback.
        re_path(
            r"^(?P<path>[^/]+\.(?:svg|png|jpe?g|gif|ico|webp|txt|json|map))$",
            serve,
            {"document_root": settings.FRONTEND_DIST},
        ),
        re_path(
            r"^(?!api/|admin/|media/|static/|assets/).*$",
            TemplateView.as_view(template_name="index.html"),
            name="spa-fallback",
        ),
    ]
