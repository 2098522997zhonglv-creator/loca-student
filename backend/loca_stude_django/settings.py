"""Local-only settings for the extracted loca_stude knowledge center."""

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
    "daphne",
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt", "django_filters", "corsheaders",
    "channels",
    "drf_spectacular", "accounts.apps.AccountsConfig", "projects", "api_keys",
    "prompts", "file_management.apps.FileManagementConfig",
    "knowledge.apps.KnowledgeConfig", "requirements", "mcp_tools.apps.McpToolsConfig",
    "skills", "langgraph_integration", "orchestrator_integration", "operation_logs",
    "testcases",
    "testcase_templates",
    "ui_automation",
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

ROOT_URLCONF = "loca_stude_django.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [FRONTEND_DIST], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "loca_stude_django.wsgi.application"
ASGI_APPLICATION = "loca_stude_django.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": Path(os.environ.get("DATABASE_PATH", DATA_DIR / "knowledge_center.sqlite3")),
    "OPTIONS": {"timeout": 30},
}}

AUTH_PASSWORD_VALIDATORS = []
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Shanghai")
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
    "DEFAULT_PAGINATION_CLASS": "loca_stude_django.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    # 与 loca_stude 一致：统一包装为 { status, code, message, data }
    "DEFAULT_RENDERER_CLASSES": (
        "loca_stude_django.renderers.UnifiedResponseRenderer",
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
    _CELERY_CONTROL_DIR = DATA_DIR / "celery" / "control"
    for _d in (_CELERY_QUEUE_DIR, _CELERY_CONTROL_DIR):
        _d.mkdir(parents=True, exist_ok=True)
    # data_folder_in 与 out 必须指向同一目录：生产者写入、worker 从这里取。
    # control_folder 存 exchange-queue 绑定表，kombu 默认取相对路径 "control"，
    # 会随进程工作目录变化；Web 与 worker 的 cwd 不同就会各看一份绑定表而收不到消息。
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        "data_folder_in": str(_CELERY_QUEUE_DIR),
        "data_folder_out": str(_CELERY_QUEUE_DIR),
        "control_folder": str(_CELERY_CONTROL_DIR),
    }
    # 不配结果后端：进度落在 ReviewReport，CELERY_TASK_IGNORE_RESULT 也已开启。
    # 而 file://<Windows 绝对路径> 会被 urlparse 把盘符当主机、后面的路径当端口，
    # worker 打印启动横幅调 backend.as_uri() 时就崩（Port could not be cast）。
    _DEFAULT_RESULT_BACKEND = ""
else:
    CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600, "max_retries": 0}
    _DEFAULT_RESULT_BACKEND = CELERY_BROKER_URL

CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", _DEFAULT_RESULT_BACKEND
)

# 默认在当前进程执行：功能可用但评审会和请求抢 GIL。
# 改为 false 走独立 worker 前，必须先确认 worker 真的在消费队列——filesystem
# broker 派发只是写文件、不会失败，worker 不消费时评审会静默卡住直到超时。
CELERY_TASK_ALWAYS_EAGER = os.environ.get(
    "CELERY_TASK_ALWAYS_EAGER", "true"
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

CELERY_BEAT_SCHEDULE = {
    "dingtalk-sync-tick": {
        "task": "knowledge.sync_due_dingtalk_bindings",
        "schedule": 900.0,  # 15 minutes
    },
}

FILE_MANAGEMENT_MAX_FILE_SIZE = int(os.environ.get("FILE_MANAGEMENT_MAX_FILE_SIZE", 104857600))
FILE_MANAGEMENT_LLM_MAX_CHARS_PER_FILE = int(os.environ.get("FILE_MANAGEMENT_LLM_MAX_CHARS_PER_FILE", 0))
LANGGRAPH_CHECKPOINT_SQLITE_PATH = os.environ.get(
    "LANGGRAPH_CHECKPOINT_SQLITE_PATH", str(DATA_DIR / "chat_history.sqlite3")
)
DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://127.0.0.1:8000")

# 知识库从 URL 拉文档时的 SSRF 策略。
# 本产品多为局域网部署，默认允许私网；生产可设 KB_URL_ALLOW_PRIVATE=false，
# 并用 KB_URL_HOST_ALLOWLIST=prototypes.netflying.net 按域名放行。
_kb_allow_private_env = os.environ.get("KB_URL_ALLOW_PRIVATE", "").strip().lower()
if _kb_allow_private_env in ("0", "false", "no", "off"):
    KB_URL_ALLOW_PRIVATE = False
elif _kb_allow_private_env in ("1", "true", "yes", "on"):
    KB_URL_ALLOW_PRIVATE = True
else:
    # 未配置时默认放行内网（本地知识中心常见场景）
    KB_URL_ALLOW_PRIVATE = True
KB_URL_HOST_ALLOWLIST = {
    h.strip().lower()
    for h in os.environ.get("KB_URL_HOST_ALLOWLIST", "").split(",")
    if h.strip()
}

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
            "()": "loca_stude_django.safe_log_handler.SafeTimedRotatingFileHandler",
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

_logging.getLogger("loca_stude_django.settings").info(
    "Runtime logging ready: file=%s level=%s backup_count=%s",
    APP_LOG_FILE,
    LOG_LEVEL,
    LOG_BACKUP_COUNT,
)
