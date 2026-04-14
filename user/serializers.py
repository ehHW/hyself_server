from django.contrib.auth import authenticate, password_validation
from django.conf import settings
from rest_framework import serializers

from user.access_context import ensure_default_user_role, ensure_user_has_minimum_role
from user.models import Permission, Role, User, UserPreference


SUPER_ADMIN_ONLY_PERMISSION_CODES = {"chat.review_all_messages"}


def _filter_permission_queryset_for_request(queryset, request):
    if request and request.user and request.user.is_authenticated and request.user.is_superuser:
        return queryset
    return queryset.exclude(code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        raw_username = str(attrs.get("username", "")).strip()
        target_user = User.objects.filter(username=raw_username).first()
        if target_user and not target_user.is_active:
            raise serializers.ValidationError("此账号已被停用，请联系管理员")

        user = authenticate(username=raw_username, password=attrs["password"])
        if not user:
            raise serializers.ValidationError("用户名或密码错误")
        if not user.is_active:
            raise serializers.ValidationError("此账号已被停用，请联系管理员")
        attrs["user"] = user
        return attrs


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "name", "description", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class RoleSerializer(serializers.ModelSerializer):
    permission_ids = serializers.PrimaryKeyRelatedField(
        source="permissions", queryset=Permission.objects.all(), many=True, write_only=True, required=False
    )
    permissions = PermissionSerializer(many=True, read_only=True)

    def get_fields(self):
        fields = super().get_fields()
        instance = self.instance if isinstance(self.instance, Role) else None
        request = self.context.get("request")
        fields["permission_ids"].queryset = _filter_permission_queryset_for_request(Permission.objects.all(), request)
        if instance and instance.is_super_admin_role():
            for field_name in ["name", "description", "permission_ids"]:
                fields[field_name].read_only = True
        return fields

    class Meta:
        model = Role
        fields = [
            "id",
            "name",
            "description",
            "permission_ids",
            "permissions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class UserSerializer(serializers.ModelSerializer):
    role_ids = serializers.PrimaryKeyRelatedField(
        source="roles", queryset=Role.objects.all(), many=True, write_only=True, required=False
    )
    roles = RoleSerializer(many=True, read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    def get_fields(self):
        fields = super().get_fields()
        fields["is_superuser"].read_only = True
        fields["is_staff"].read_only = True

        instance = self.instance if isinstance(self.instance, User) else None
        if instance is not None:
            fields["username"].read_only = True
            if instance.has_super_admin_role():
                for field_name in ["email", "display_name", "avatar", "phone_number", "is_active", "role_ids"]:
                    fields[field_name].read_only = True
        return fields

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "password",
            "email",
            "display_name",
            "avatar",
            "phone_number",
            "is_active",
            "is_staff",
            "is_superuser",
            "roles",
            "role_ids",
            "last_login",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        read_only_fields = ["id", "last_login", "created_at", "updated_at", "deleted_at", "is_superuser", "is_staff"]

    def validate(self, attrs):
        roles = attrs.get("roles", serializers.empty)
        if self.instance is not None and roles is not serializers.empty and len(list(roles)) == 0:
            raise serializers.ValidationError({"role_ids": "每个用户至少需要保留一个角色"})
        return attrs

    def _validate_roles(self, roles):
        if any(role.is_super_admin_role() for role in roles):
            raise serializers.ValidationError({"role_ids": "禁止分配超级管理员角色"})

    def _resolve_create_roles(self, roles):
        resolved_roles = list(roles)
        if not resolved_roles:
            resolved_roles = [ensure_default_user_role()]
        self._validate_roles(resolved_roles)
        return resolved_roles

    def _validate_update_roles(self, roles):
        resolved_roles = list(roles)
        self._validate_roles(resolved_roles)
        if not resolved_roles:
            raise serializers.ValidationError({"role_ids": "每个用户至少需要保留一个角色"})
        return resolved_roles

    def create(self, validated_data):
        roles = self._resolve_create_roles(validated_data.pop("roles", []))
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "创建用户时必须提供密码"})
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        user.roles.set(roles)
        return user

    def update(self, instance, validated_data):
        roles = validated_data.pop("roles", None)
        password = validated_data.pop("password", None)
        if roles is not None:
            roles = self._validate_update_roles(roles)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        if roles is not None:
            instance.roles.set(roles)
        else:
            ensure_user_has_minimum_role(instance)
        return instance


class ProfileSerializer(serializers.ModelSerializer):
    roles = RoleSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "display_name",
            "avatar",
            "phone_number",
            "is_superuser",
            "roles",
            "created_at",
            "updated_at",
            "deleted_at",
        ]


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["email", "display_name", "avatar", "phone_number"]

    def validate_avatar(self, value):
        avatar = str(value or "").strip()
        if not avatar:
            return ""

        media_prefix = settings.MEDIA_URL.rstrip("/") + "/"
        if not avatar.startswith(media_prefix):
            raise serializers.ValidationError("头像地址不合法")
        if "/avatars/" not in avatar:
            raise serializers.ValidationError("头像地址必须来自头像上传目录")
        if ".." in avatar:
            raise serializers.ValidationError("头像地址不合法")
        if len(avatar) > 500:
            raise serializers.ValidationError("头像地址过长")
        return avatar

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save(update_fields=[*validated_data.keys(), "updated_at"])
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        current_password = attrs.get("current_password", "")
        new_password = attrs.get("new_password", "")
        confirm_password = attrs.get("confirm_password", "")

        if not user or not user.is_authenticated:
            raise serializers.ValidationError("当前登录状态无效")
        if not user.check_password(current_password):
            raise serializers.ValidationError({"current_password": "当前密码错误"})
        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "两次输入的新密码不一致"})
        if current_password == new_password:
            raise serializers.ValidationError({"new_password": "新密码不能与当前密码相同"})

        password_validation.validate_password(new_password, user)
        attrs["user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "theme_mode",
            "chat_receive_notification",
            "chat_list_sort_mode",
            "chat_stealth_inspect_enabled",
            "settings_json",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_theme_mode(self, value):
        return "dark" if value == "dark" else "light"

    def validate_chat_list_sort_mode(self, value):
        normalized = str(value or "recent").strip().lower()
        return normalized if normalized in {"recent", "unread"} else "recent"

    def validate_chat_stealth_inspect_enabled(self, value):
        request = self.context.get("request")
        if value and (not request or not request.user or not request.user.is_superuser):
            raise serializers.ValidationError("仅超级管理员可开启隐身巡检")
        return bool(value)

    def validate_settings_json(self, value):
        return value or {}
