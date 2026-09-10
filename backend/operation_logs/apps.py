from django.apps import AppConfig

class OperationLogsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'operation_logs'
    verbose_name = '操作日志管理'
