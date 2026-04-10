from rest_framework import serializers


class FriendRequestCreateSerializer(serializers.Serializer):
    to_user_id = serializers.IntegerField(min_value=1)
    request_message = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class FriendRequestHandleSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["accept", "reject", "cancel"])


class FriendSettingUpdateSerializer(serializers.Serializer):
    remark = serializers.CharField(max_length=80, required=False, allow_blank=True)