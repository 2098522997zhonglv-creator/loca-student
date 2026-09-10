from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('file_management', '0004_alter_filereference_ref_type')]
    operations = [
        migrations.AlterField(
            model_name='filereference',
            name='ref_type',
            field=models.CharField(choices=[('llm_chat', 'LLM对话')], max_length=50),
        ),
    ]
