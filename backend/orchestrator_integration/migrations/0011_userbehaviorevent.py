# Generated manually for UserBehaviorEvent

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0001_initial"),
        ("orchestrator_integration", "0010_delete_agent_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBehaviorEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("tool_call", "工具调用"),
                            ("user_correction", "用户纠正"),
                            ("hitl_decision", "HITL 审批"),
                        ],
                        max_length=32,
                        verbose_name="事件类型",
                    ),
                ),
                ("summary_text", models.TextField(verbose_name="摘要文本")),
                (
                    "metadata",
                    models.JSONField(blank=True, default=dict, verbose_name="元数据"),
                ),
                (
                    "vector_id",
                    models.CharField(
                        blank=True, default="", max_length=64, verbose_name="向量点 ID"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="behavior_events",
                        to="projects.project",
                        verbose_name="项目",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="behavior_events",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "账号行为事件",
                "verbose_name_plural": "账号行为事件",
                "db_table": "orchestrator_user_behavior_event",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="userbehaviorevent",
            index=models.Index(
                fields=["user", "-created_at"], name="orchestrato_user_id_ff9ca2_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="userbehaviorevent",
            index=models.Index(
                fields=["user", "event_type"], name="orchestrato_user_id_d87e4d_idx"
            ),
        ),
    ]
