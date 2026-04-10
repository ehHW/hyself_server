from __future__ import annotations

from rest_framework import serializers


class ChatSendMessageWsSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    content = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)
    client_message_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    quoted_message_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_client_message_id(self, value: str) -> str | None:
        normalized = value.strip()
        return normalized or None


class ChatSendAssetMessageWsSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    asset_reference_id = serializers.IntegerField(min_value=1)
    client_message_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    quoted_message_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_client_message_id(self, value: str) -> str | None:
        normalized = value.strip()
        return normalized or None


class ChatMarkReadWsSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    last_read_sequence = serializers.IntegerField(min_value=0)


class ChatTypingWsSerializer(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)
    is_typing = serializers.BooleanField(required=False, default=False)


class UploadTaskSubscriptionWsSerializer(serializers.Serializer):
    task_id = serializers.CharField(allow_blank=False, trim_whitespace=True, max_length=255)


class EchoWsSerializer(serializers.Serializer):
    message = serializers.CharField(allow_blank=False, trim_whitespace=True)