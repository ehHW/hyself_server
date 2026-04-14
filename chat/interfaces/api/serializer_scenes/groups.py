from rest_framework import serializers

from chat.models import ChatConversationMember, ChatGroupConfig


class InviteConversationMemberSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField(min_value=1)


class ApplyGroupInvitationSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    inviter_user_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class UpdateConversationMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[ChatConversationMember.Role.ADMIN, ChatConversationMember.Role.MEMBER])


class MuteConversationMemberSerializer(serializers.Serializer):
    mute_minutes = serializers.IntegerField(min_value=0, max_value=60 * 24 * 30)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class TransferGroupOwnerSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField(min_value=1)


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


class GroupJoinRequestHandleSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject", "cancel"])
    review_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")