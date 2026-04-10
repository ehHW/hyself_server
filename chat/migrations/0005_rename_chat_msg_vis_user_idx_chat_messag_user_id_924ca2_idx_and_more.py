from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_chatmessagevisibility"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="chatmessagevisibility",
            old_name="chat_msg_vis_user_idx",
            new_name="chat_messag_user_id_924ca2_idx",
        ),
        migrations.RenameIndex(
            model_name="chatmessagevisibility",
            old_name="chat_msg_vis_msg_idx",
            new_name="chat_messag_message_4c8ea4_idx",
        ),
        migrations.AlterField(
            model_name="chatgroupjoinrequest",
            name="request_type",
            field=models.CharField(
                choices=[("invite", "邀请"), ("application", "申请")],
                default="invite",
                max_length=20,
            ),
        ),
    ]