from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import exception_handler


class PermissionDeniedError(PermissionDenied):
    default_detail = "权限不足"
    default_code = "permission_denied"


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, PermissionDenied) and response.status_code == status.HTTP_403_FORBIDDEN:
        detail = str(getattr(exc, "detail", "") or exc or "权限不足")
        response.data = {
            "detail": detail,
            "error_code": "permission_denied",
        }
    return response