import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from utils.soft_delete import SoftDeleteModel


def build_pair_key(user_a_id: int, user_b_id: int) -> str:
    low_id, high_id = sorted([int(user_a_id), int(user_b_id)])
    return hashlib.sha256(f"{low_id}:{high_id}".encode("utf-8")).hexdigest()


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ChatFriendRequest(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        ACCEPTED = "accepted", "已通过"
        REJECTED = "rejected", "已拒绝"
        CANCELED = "canceled", "已取消"
        EXPIRED = "expired", "已过期"

    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_chat_friend_requests")
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_chat_friend_requests")
    pair_key = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    request_message = models.CharField(max_length=255, blank=True, default="")
    auto_accepted = models.BooleanField(default=False)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="handled_chat_friend_requests")
    handled_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "chat_friend_request"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["from_user", "status", "created_at"]),
            models.Index(fields=["to_user", "status", "created_at"]),
            models.Index(fields=["pair_key", "status", "created_at"]),
            models.Index(fields=["from_user", "to_user", "status"]),
        ]


class ChatFriendship(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        DELETED = "deleted", "已删除"

    pair_key = models.CharField(max_length=64, unique=True)
    user_low = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_friendships_low")
    user_high = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_friendships_high")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    source_request = models.ForeignKey("chat.ChatFriendRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="friendships")
    accepted_at = models.DateTimeField(default=timezone.now)
    remark_low = models.CharField(max_length=80, blank=True, default="")
    remark_high = models.CharField(max_length=80, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "chat_friendship"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["user_low", "status"]),
            models.Index(fields=["user_high", "status"]),
        ]


class ChatConversation(SoftDeleteModel):
    class Type(models.TextChoices):
        DIRECT = "direct", "单聊"
        GROUP = "group", "群聊"

    class Status(models.TextChoices):
        ACTIVE = "active", "正常"
        DISBANDED = "disbanded", "已解散"

    type = models.CharField(max_length=20, choices=Type.choices)
    name = models.CharField(max_length=150, blank=True, default="")
    avatar = models.CharField(max_length=500, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_chat_conversations")
    direct_pair_key = models.CharField(max_length=64, unique=True, null=True, blank=True, default=None)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    last_message = models.ForeignKey("chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    last_message_preview = models.CharField(max_length=255, blank=True, default="")
    last_message_at = models.DateTimeField(null=True, blank=True, default=None)
    member_count_cache = models.IntegerField(default=0)

    class Meta:
        db_table = "chat_conversation"
        ordering = ["-last_message_at", "-id"]
        indexes = [
            models.Index(fields=["type", "status", "updated_at"]),
            models.Index(fields=["owner", "status", "updated_at"]),
            models.Index(fields=["last_message_at"]),
            models.Index(fields=["name"]),
        ]


class ChatGroupConfig(SoftDeleteModel):
    conversation = models.OneToOneField("chat.ChatConversation", on_delete=models.CASCADE, related_name="group_config")
    join_approval_required = models.BooleanField(default=False)
    allow_member_invite = models.BooleanField(default=True)
    max_members = models.IntegerField(null=True, blank=True, default=None)
    mute_all = models.BooleanField(default=False)

    class Meta:
        db_table = "chat_group_config"
        ordering = ["id"]
        indexes = [models.Index(fields=["join_approval_required"])]


class ChatGroupJoinRequest(TimestampedModel):
    class RequestType(models.TextChoices):
        INVITE = "invite", "邀请"
        APPLICATION = "application", "申请"

    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已拒绝"
        CANCELED = "canceled", "已取消"
        EXPIRED = "expired", "已过期"

    conversation = models.ForeignKey("chat.ChatConversation", on_delete=models.CASCADE, related_name="join_requests")
    request_type = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.INVITE)
    inviter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_chat_group_join_requests")
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="targeted_chat_group_join_requests")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_chat_group_join_requests")
    review_note = models.CharField(max_length=255, blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "chat_group_join_request"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["conversation", "status", "created_at"]),
            models.Index(fields=["target_user", "status", "created_at"]),
            models.Index(fields=["inviter", "status", "created_at"]),
            models.Index(fields=["conversation", "target_user", "status"]),
        ]


class ChatConversationMember(TimestampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "群主"
        ADMIN = "admin", "管理员"
        MEMBER = "member", "成员"

    class Status(models.TextChoices):
        ACTIVE = "active", "正常"
        LEFT = "left", "已退出"
        REMOVED = "removed", "已移除"

    conversation = models.ForeignKey("chat.ChatConversation", on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True, default=None)
    removed_at = models.DateTimeField(null=True, blank=True, default=None)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="removed_chat_members")
    mute_until = models.DateTimeField(null=True, blank=True, default=None)
    mute_reason = models.CharField(max_length=255, blank=True, default="")
    is_pinned = models.BooleanField(default=False)
    show_in_list = models.BooleanField(default=True)
    unread_count = models.IntegerField(default=0)
    last_read_message = models.ForeignKey("chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    last_read_sequence = models.BigIntegerField(default=0)
    last_delivered_message = models.ForeignKey("chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    last_delivered_sequence = models.BigIntegerField(default=0)
    extra_settings = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "chat_conversation_member"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="uniq_chat_conversation_member")]
        indexes = [
            models.Index(fields=["user", "status", "updated_at"]),
            models.Index(fields=["user", "show_in_list", "updated_at"]),
            models.Index(fields=["conversation", "status", "role"]),
            models.Index(fields=["conversation", "unread_count"]),
        ]


class ChatMessage(TimestampedModel):
    class MessageType(models.TextChoices):
        TEXT = "text", "文本"
        SYSTEM = "system", "系统"
        IMAGE = "image", "图片"
        FILE = "file", "文件"
        CHAT_RECORD = "chat_record", "聊天记录"

    conversation = models.ForeignKey("chat.ChatConversation", on_delete=models.CASCADE, related_name="messages")
    sequence = models.BigIntegerField()
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_chat_messages")
    message_type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.TEXT)
    content = models.TextField(blank=True, default="")
    payload = models.JSONField(blank=True, default=dict)
    client_message_id = models.CharField(max_length=64, unique=True, null=True, blank=True, default=None)
    is_system = models.BooleanField(default=False)

    class Meta:
        db_table = "chat_message"
        ordering = ["sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["conversation", "sequence"], name="uniq_chat_message_conversation_sequence")]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["conversation", "id"]),
            models.Index(fields=["sender", "created_at"]),
        ]


class ChatMessageVisibility(TimestampedModel):
    message = models.ForeignKey("chat.ChatMessage", on_delete=models.CASCADE, related_name="hidden_entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hidden_chat_messages")

    class Meta:
        db_table = "chat_message_visibility"
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="uniq_chat_message_visibility_message_user")]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["message", "created_at"]),
        ]
