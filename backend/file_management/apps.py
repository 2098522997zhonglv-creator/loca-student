from django.apps import AppConfig


class FileManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'file_management'
    verbose_name = '文件管理'

    def ready(self):
        import file_management.signals
