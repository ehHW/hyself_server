from __future__ import annotations

from rest_framework.exceptions import PermissionDenied


def ensure_chat_permission(user, permission_code: str, detail: str) -> None:
    if getattr(user, "is_superuser", False):
        return
    if not getattr(user, "is_authenticated", False) or not user.has_permission_code(permission_code):
        raise PermissionDenied(detail)