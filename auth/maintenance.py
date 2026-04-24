from __future__ import annotations

import json

from django.contrib.auth import authenticate
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication

from hyself.system_runtime import (
    SYSTEM_MAINTENANCE_STATUS_CODE,
    build_maintenance_response_payload,
    ensure_maintenance_activated,
    get_system_setting,
    is_maintenance_active,
)


class SystemMaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_authentication = JWTAuthentication()

    def __call__(self, request):
        if self._should_skip(request):
            return self.get_response(request)

        setting = get_system_setting()
        if not is_maintenance_active(setting):
            return self.get_response(request)

        user = self._resolve_user(request)
        if user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
            ensure_maintenance_activated(actor=user)
            return self.get_response(request)

        if self._allow_superadmin_login(request):
            return self.get_response(request)

        ensure_maintenance_activated()
        return JsonResponse(
            build_maintenance_response_payload(setting),
            status=SYSTEM_MAINTENANCE_STATUS_CODE,
        )

    def _should_skip(self, request) -> bool:
        path = str(getattr(request, "path", "") or "")
        if request.method == "OPTIONS":
            return True
        if not path.startswith("/api1/"):
            return True
        if path.startswith("/api1/uploads/"):
            return True
        return False

    def _resolve_user(self, request):
        try:
            auth_result = self.jwt_authentication.authenticate(request)
        except Exception:
            auth_result = None
        if not auth_result:
            return None
        user, _ = auth_result
        return user

    def _allow_superadmin_login(self, request) -> bool:
        path = str(getattr(request, "path", "") or "")
        if path != "/api1/auth/login/" or request.method != "POST":
            return False
        try:
            body = json.loads((request.body or b"{}").decode("utf-8") or "{}")
        except Exception:
            return False
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or not password:
            return False
        user = authenticate(username=username, password=password)
        return bool(user and user.is_active and user.is_superuser)