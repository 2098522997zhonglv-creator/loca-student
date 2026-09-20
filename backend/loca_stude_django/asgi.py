"""ASGI entry with HTTP + UI-automation WebSocket support."""

import os

os.umask(0o002)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "loca_stude_django.settings")

import django

django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

from ui_automation.routing import websocket_urlpatterns as ui_ws_patterns

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(URLRouter(ui_ws_patterns)),
    }
)
