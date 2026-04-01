from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bbot", "0003_remove_uploadedfile_original_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadedfile",
            name="is_system",
            field=models.BooleanField(db_index=True, default=False, verbose_name="是否系统内置"),
        ),
        migrations.AddField(
            model_name="uploadedfile",
            name="recycled_at",
            field=models.DateTimeField(blank=True, db_index=True, default=None, null=True, verbose_name="移入回收站时间"),
        ),
    ]
