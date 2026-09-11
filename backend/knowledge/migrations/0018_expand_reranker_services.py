from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0017_remove_document_image"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgeglobalconfig",
            name="reranker_service",
            field=models.CharField(
                choices=[
                    ("none", "不启用"),
                    ("xinference", "Xinference"),
                    ("openai_compatible", "OpenAI 兼容 /v1/rerank"),
                    ("tei", "TEI / Infinity（开源 HTTP）"),
                    ("jina", "Jina Reranker"),
                    ("cohere", "Cohere 兼容"),
                    ("custom", "自定义 API（兼容 OpenAI 风格）"),
                ],
                default="none",
                help_text="选择Reranker精排服务，可独立于嵌入服务配置",
                max_length=50,
                verbose_name="Reranker服务",
            ),
        ),
        migrations.AlterField(
            model_name="knowledgeglobalconfig",
            name="reranker_model_name",
            field=models.CharField(
                blank=True,
                default="bge-reranker-v2-m3",
                help_text="开源重排模型名，如 bge-reranker-v2-m3、Qwen3-Reranker、jina-reranker-v2",
                max_length=100,
                verbose_name="Reranker模型名称",
            ),
        ),
    ]
