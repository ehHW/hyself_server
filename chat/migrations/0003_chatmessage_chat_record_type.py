from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_friendship_remarks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatmessage",
            name="message_type",
            field=models.CharField(
                choices=[
                    ("text", "文本"),
                    ("system", "系统"),
                    ("image", "图片"),
                    ("file", "文件"),
                    ("chat_record", "聊天记录"),
                ],
                default="text",
                max_length=20,
            ),
        ),
    ]