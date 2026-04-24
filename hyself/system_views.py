from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from hyself.system_runtime import (
    build_system_settings_payload,
    create_announcement,
    delete_announcement,
    list_announcements_for_user,
    mark_all_announcements_read,
    mark_announcement_read,
    resolve_announcement_content_max_length,
    update_system_setting,
)


def _parse_optional_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValueError("维护时间不合法")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class SystemSettingsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_system_settings_payload())

    def patch(self, request):
        if not request.user.is_superuser:
            return Response({"detail": "仅超级管理员可修改系统设置"}, status=status.HTTP_403_FORBIDDEN)

        try:
            maintenance_scheduled_at = _parse_optional_datetime(request.data.get("maintenance_scheduled_at"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = update_system_setting(
            actor=request.user,
            system_title=request.data.get("system_title") if "system_title" in request.data else None,
            maintenance_enabled=request.data.get("maintenance_enabled") if "maintenance_enabled" in request.data else None,
            maintenance_scheduled_at=maintenance_scheduled_at,
        )
        return Response(payload)


class SystemAnnouncementListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"items": list_announcements_for_user(request.user)})

    def post(self, request):
        ensure_request_permission(request, "system.publish_announcement")
        title = str(request.data.get("title", "")).strip()
        content = str(request.data.get("content", "")).strip()
        if not title:
            return Response({"detail": "公告标题不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        if not content:
            return Response({"detail": "公告内容不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        if len(title) > 255:
            return Response({"detail": "公告标题不能超过255个字符"}, status=status.HTTP_400_BAD_REQUEST)
        max_length = resolve_announcement_content_max_length()
        if len(content) > max_length:
            return Response({"detail": f"公告内容不能超过{max_length}个字符"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = create_announcement(actor=request.user, title=title, content=content)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "公告已发布", "item": payload}, status=status.HTTP_201_CREATED)


class SystemAnnouncementReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, announcement_id: int):
        try:
            payload = mark_announcement_read(user=request.user, announcement_id=announcement_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"detail": "已标记已读", "item": payload})


class SystemAnnouncementReadAllAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        payload = mark_all_announcements_read(user=request.user)
        return Response({"detail": "已全部标记已读", **payload})


class SystemAnnouncementDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, announcement_id: int):
        ensure_request_permission(request, "system.publish_announcement")
        try:
            delete_announcement(announcement_id=announcement_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"detail": "公告已删除"})