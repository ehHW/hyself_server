from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_chatmessage_chat_record_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatMessageVisibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hidden_entries", to="chat.chatmessage")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hidden_chat_messages", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "chat_message_visibility",
                "ordering": ["-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="chatmessagevisibility",
            constraint=models.UniqueConstraint(fields=("message", "user"), name="uniq_chat_message_visibility_message_user"),
        ),
        migrations.AddIndex(
            model_name="chatmessagevisibility",
            index=models.Index(fields=["user", "created_at"], name="chat_msg_vis_user_idx"),
        ),
        migrations.AddIndex(
            model_name="chatmessagevisibility",
            index=models.Index(fields=["message", "created_at"], name="chat_msg_vis_msg_idx"),
        ),
    ]