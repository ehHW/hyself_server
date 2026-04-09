from django.contrib.auth import get_user_model
from rest_framework import serializers

from bbot.models import AssetReference
from chat.models import ChatConversationMember, ChatGroupConfig
from user.models import UserPreference


User = get_user_model()


class FriendRequestCreateSerializer(serializers.Serializer):
    to_user_id = serializers.IntegerField(min_value=1)
    request_message = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class FriendRequestHandleSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["accept", "reject", "cancel"])


class OpenDirectConversationSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField(min_value=1)


class CreateGroupConversationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    member_user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    join_approval_required = serializers.BooleanField(required=False, default=False)
    allow_member_invite = serializers.BooleanField(required=False, default=True)


class ConversationReadSerializer(serializers.Serializer):
    last_read_sequence = serializers.IntegerField(min_value=0)


class InviteConversationMemberSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField(min_value=1)


class ApplyGroupInvitationSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    inviter_user_id = serializers.IntegerField(min_value=1)


class UpdateConversationMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[ChatConversationMember.Role.ADMIN, ChatConversationMember.Role.MEMBER])


class MuteConversationMemberSerializer(serializers.Serializer):
    mute_minutes = serializers.IntegerField(min_value=0, max_value=60 * 24 * 30)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class GroupConfigUpdateSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=150, required=False, allow_blank=False)
    avatar = serializers.CharField(max_length=500, required=False, allow_blank=True)

    class Meta:
        model = ChatGroupConfig
        fields = ["name", "avatar", "join_approval_required", "allow_member_invite", "max_members", "mute_all"]

    def validate_max_members(self, value):
        if value is None:
            return None
        if value <= 0:
            raise serializers.ValidationError("群成员上限必须大于 0")
        return value


class ConversationPinSerializer(serializers.Serializer):
    is_pinned = serializers.BooleanField()


class ConversationPreferenceSerializer(serializers.Serializer):
    mute_notifications = serializers.BooleanField(required=False)
    group_nickname = serializers.CharField(max_length=80, required=False, allow_blank=True)


class AttachmentMessageCreateSerializer(serializers.Serializer):
    asset_reference_id = serializers.IntegerField(min_value=1)

    def validate_asset_reference_id(self, value):
        if not AssetReference.objects.filter(id=value).exists():
            raise serializers.ValidationError("资产引用不存在")
        return value


class ForwardMessagesSerializer(serializers.Serializer):
    target_conversation_id = serializers.IntegerField(min_value=1)
    message_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    forward_mode = serializers.ChoiceField(choices=["separate", "merged"], required=False, default="separate")


class FriendSettingUpdateSerializer(serializers.Serializer):
    remark = serializers.CharField(max_length=80, required=False, allow_blank=True)


class GroupJoinRequestHandleSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject", "cancel"])
    review_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "theme_mode",
            "chat_receive_notification",
            "chat_list_sort_mode",
            "chat_stealth_inspect_enabled",
            "settings_json",
        ]

    def validate_theme_mode(self, value):
        return "dark" if value == "dark" else "light"

    def validate_chat_list_sort_mode(self, value):
        normalized = str(value or "recent").strip().lower()
        if normalized not in {"recent", "unread"}:
            raise serializers.ValidationError("排序方式不支持")
        return normalized

    def validate_chat_stealth_inspect_enabled(self, value):
        request = self.context.get("request")
        if value and (not request or not request.user or not request.user.is_superuser):
            raise serializers.ValidationError("仅超级管理员可开启隐身巡检")
        return bool(value)

    def validate_settings_json(self, value):
        return value or {}