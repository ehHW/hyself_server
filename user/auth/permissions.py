from auth.permissions import AuthenticatedPermission, SuperAdminPermission
from rest_framework.permissions import BasePermission


class ActionPermission(BasePermission):
    """基于 ViewSet action 映射到 RBAC code 的权限检查。"""

    message = "权限不足"
    code = "permission_denied"

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        required_map = getattr(view, "required_permission_map", {})
        action = getattr(view, "action", None)
        perm_code = required_map.get(action)
        if not perm_code:
            return True
        return request.user.has_permission_code(perm_code)


class SuperAdminOnly(SuperAdminPermission):
    pass