from __future__ import annotations

from rest_framework.permissions import BasePermission


class AuthenticatedPermission(BasePermission):
    """项目级通用认证权限，只负责确认当前请求已登录。"""

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)


class SuperAdminPermission(AuthenticatedPermission):
    """项目级通用超管权限，不包含业务侧的资源判断。"""

    def has_permission(self, request, view) -> bool:
        return bool(super().has_permission(request, view) and request.user.is_superuser)