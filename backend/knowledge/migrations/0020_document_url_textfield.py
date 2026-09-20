from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0019_dingtalk_sync"),
    ]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="url",
            field=models.TextField(blank=True, null=True, verbose_name="网页链接"),
        ),
    ]
