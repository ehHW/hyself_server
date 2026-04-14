from rest_framework import serializers

from hyself.models import AssetReference


class OpenDirectConversationSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField(min_value=1)


class CreateGroupConversationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    member_user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    join_approval_required = serializers.BooleanField(required=False, default=False)
    allow_member_invite = serializers.BooleanField(required=False, default=True)


class ConversationReadSerializer(serializers.Serializer):
    last_read_sequence = serializers.IntegerField(min_value=0)


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