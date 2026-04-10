from rest_framework import serializers

from user.models import UserPreference


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