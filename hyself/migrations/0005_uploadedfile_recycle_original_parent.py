from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hyself", "0004_uploadedfile_is_system_uploadedfile_recycled_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadedfile",
            name="recycle_original_parent",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recycle_restorables", to="hyself.uploadedfile", verbose_name="回收站原父目录"),
        ),
    ]
