from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hyself", "0009_systemsetting_systemannouncement_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetting",
            name="announcement_content_max_length",
            field=models.PositiveIntegerField(default=300, verbose_name="公告内容最大字数"),
        ),
    ]
