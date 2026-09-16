"""Local-only settings for the extracted WHartTest knowledge center."""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load project-root .env so Windows can start without manually exporting vars.
load_dotenv(PROJECT_ROOT / ".env", override=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-knowledge-center-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = [item.strip() for item in os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,192.168.32.138"
).split(",") if item.strip()]
# Local physical-machine access: keep LAN IP even if an old .env omitted it.
if DEBUG:
    for host in ("127.0.0.1", "localhost", "192.168.32.138"):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)
CSRF_TRUSTED_ORIGINS = [item.strip() for item in os.environ.get(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000,http://192.168.32.138:8000",
).split(",") if item.strip()]
if DEBUG:
    for origin in (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://192.168.32.138:8000",
    ):
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt", "django_filters", "corsheaders",
    "drf_spectacular", "accounts.apps.AccountsConfig", "projects", "api_keys",
    "prompts", "file_management.apps.FileManagementConfig",
    "knowledge.apps.KnowledgeConfig", "requirements", "mcp_tools.apps.McpToolsConfig",
    "skills", "langgraph_integration", "orchestrator_integration", "operation_logs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "operation_logs.middleware.OperationLogMiddleware",
]

LANGUAGE_CODE = "zh-Hans"
USE_I18N = True
LANGUAGES = [
    ("zh-Hans", "简体中文"),
    ("en", "English"),
]

ROOT_URLCONF = "wharttest_django.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [FRONTEND_DIST], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "wharttest_django.wsgi.application"
ASGI_APPLICATION = "wharttest_django.asgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": Path(os.environ.get("DATABASE_PATH", DATA_DIR / "knowledge_center.sqlite3")),
    "OPTIONS": {"timeout": 30},
}}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", DATA_DIR / "media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [item.strip() for item in os.environ.get(
    "DJANGO_CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
).split(",") if item.strip()]
CORS_ALLOW_CREDENTIALS = True
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "api_keys.authentication.APIKeyAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "wharttest_django.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    # 与 WHartTest 一致：统一包装为 { status, code, message, data }
    "DEFAULT_RENDERER_CLASSES": (
        "wharttest_django.renderers.UnifiedResponseRenderer",
    ),
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}
SPECTACULAR_SETTINGS = {"TITLE": "Local Knowledge Center API", "VERSION": "1.0.0"}

# Celery：评审等长任务跑在独立 worker 进程里，与 Web 进程隔离。
# 同进程执行会让评审线程和请求处理抢同一个 GIL，拖慢 LLM 读取甚至触发超时。
# 默认用文件系统当 broker：无需安装 Redis，worker 仍是独立进程。
# 需要更低派发延迟或多 worker 时改用 redis://127.0.0.1:6379/0。
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "filesystem://")

if CELERY_BROKER_URL.startswith("filesystem://"):
    _CELERY_QUEUE_DIR = DATA_DIR / "celery" / "queue"
    _CELERY_DONE_DIR = DATA_DIR / "celery" / "processed"
    _CELERY_RESULT_DIR = DATA_DIR / "celery" / "results"
    for _d in (_CELERY_QUEUE_DIR, _CELERY_DONE_DIR, _CELERY_RESULT_DIR):
        _d.mkdir(parents=True, exist_ok=True)
    # data_folder_in 与 out 必须指向同一目录：生产者写入、worker 从这里取。
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        "data_folder_in": str(_CELERY_QUEUE_DIR),
        "data_folder_out": str(_CELERY_QUEUE_DIR),
        "data_folder_processed": str(_CELERY_DONE_DIR),
    }
    _DEFAULT_RESULT_BACKEND = f"file://{_CELERY_RESULT_DIR}"
else:
    CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600, "max_retries": 0}
    _DEFAULT_RESULT_BACKEND = CELERY_BROKER_URL

CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", _DEFAULT_RESULT_BACKEND
)

# 没有可用 broker 时设为 true，任务会退回当前进程执行（功能可用但性能受限）。
CELERY_TASK_ALWAYS_EAGER = os.environ.get(
    "CELERY_TASK_ALWAYS_EAGER", "false"
).strip().lower() in ("1", "true", "yes")
CELERY_TASK_EAGER_PROPAGATES = True

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_RESULT_EXPIRES = 3600

# 评审进度通过 ReviewReport 落库，无需 Celery 结果。开启后派发不再订阅结果
# 通道，避免 result backend 不可用时把 HTTP 请求拖上好几秒。
CELERY_TASK_IGNORE_RESULT = True

FILE_MANAGEMENT_MAX_FILE_SIZE = int(os.environ.get("FILE_MANAGEMENT_MAX_FILE_SIZE", 104857600))
FILE_MANAGEMENT_LLM_MAX_CHARS_PER_FILE = int(os.environ.get("FILE_MANAGEMENT_LLM_MAX_CHARS_PER_FILE", 0))
LANGGRAPH_CHECKPOINT_SQLITE_PATH = os.environ.get(
    "LANGGRAPH_CHECKPOINT_SQLITE_PATH", str(DATA_DIR / "chat_history.sqlite3")
)
DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://127.0.0.1:8000")

# Unified runtime logs under data/logs (same folder used by Windows auto-update scripts).
LOG_DIR = Path(os.environ.get("LOG_DIR", DATA_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", "14"))
APP_LOG_FILE = LOG_DIR / "app.log"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": LOG_LEVEL,
        },
        "file": {
            "()": "wharttest_django.safe_log_handler.SafeTimedRotatingFileHandler",
            "filename": str(APP_LOG_FILE),
            "when": "midnight",
            "interval": 1,
            "backupCount": LOG_BACKUP_COUNT,
            "encoding": "utf-8",
            "formatter": "standard",
            "level": LOG_LEVEL,
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "knowledge": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "langgraph_integration": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "orchestrator_integration": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "requirements": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "operation_logs": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "django.server": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}

# Ensure file handler is active as soon as settings load (before first request).
import logging.config as _logging_config

_logging_config.dictConfig(LOGGING)
import logging as _logging

_logging.getLogger("wharttest_django.settings").info(
    "Runtime logging ready: file=%s level=%s backup_count=%s",
    APP_LOG_FILE,
    LOG_LEVEL,
    LOG_BACKUP_COUNT,
)
