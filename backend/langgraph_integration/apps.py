from django.apps import AppConfig


class LanggraphIntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'langgraph_integration'

    def ready(self):
        from . import checks  # noqa: F401
