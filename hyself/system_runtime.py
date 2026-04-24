from __future__ import annotations

from dataclasses import dataclass

from celery import current_app
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError, transaction
from django.utils import timezone

from hyself.models import SystemAnnouncement, SystemAnnouncementRead, SystemSetting
from ws.events import notify_all_non_superusers_force_logout, notify_all_users_event


User = get_user_model()
SYSTEM_MAINTENANCE_ERROR_CODE = "system_maintenance"
SYSTEM_MAINTENANCE_STATUS_CODE = 503
DEFAULT_ANNOUNCEMENT_CONTENT_MAX_LENGTH = 300


@dataclass(frozen=True)
class MaintenanceState:
    enabled: bool
    scheduled_at: object | None
    activated_at: object | None
    is_active: bool


def get_system_setting() -> SystemSetting:
    try:
        setting, _ = SystemSetting.objects.get_or_create(singleton_key="default")
        return setting
    except (ProgrammingError, OperationalError):
        # During rollout before migrations are applied, keep auth endpoints available.
        return SystemSetting(singleton_key="default")


def resolve_system_title() -> str:
    setting = get_system_setting()
    configured_title = str(setting.system_title or "").strip()
    if configured_title:
        return configured_title
    return str(getattr(settings, "SYSTEM_TITLE", "Hyself 管理后台") or "Hyself 管理后台")


def is_maintenance_active(setting: SystemSetting | None = None, *, now=None) -> bool:
    current_time = now or timezone.now()
    target = setting or get_system_setting()
    if not target.maintenance_enabled:
        return False
    if target.maintenance_scheduled_at is None:
        return True
    return target.maintenance_scheduled_at <= current_time


def is_current_maintenance_active() -> bool:
    return is_maintenance_active(get_system_setting())


def build_maintenance_state_payload(setting: SystemSetting | None = None) -> dict[str, object]:
    target = setting or get_system_setting()
    return {
        "enabled": bool(target.maintenance_enabled),
        "scheduled_at": target.maintenance_scheduled_at.isoformat() if target.maintenance_scheduled_at else None,
        "activated_at": target.maintenance_activated_at.isoformat() if target.maintenance_activated_at else None,
        "is_active": is_maintenance_active(target),
    }


def build_system_settings_payload() -> dict[str, object]:
    setting = get_system_setting()
    return {
        "system_title": resolve_system_title(),
        "announcement_content_max_length": resolve_announcement_content_max_length(setting),
        "maintenance": build_maintenance_state_payload(setting),
    }


def resolve_announcement_content_max_length(setting: SystemSetting | None = None) -> int:
    target = setting or get_system_setting()
    try:
        configured = int(getattr(target, "announcement_content_max_length", DEFAULT_ANNOUNCEMENT_CONTENT_MAX_LENGTH) or DEFAULT_ANNOUNCEMENT_CONTENT_MAX_LENGTH)
    except (TypeError, ValueError):
        configured = DEFAULT_ANNOUNCEMENT_CONTENT_MAX_LENGTH
    return max(1, min(DEFAULT_ANNOUNCEMENT_CONTENT_MAX_LENGTH, configured))


def build_maintenance_response_payload(setting: SystemSetting | None = None) -> dict[str, object]:
    target = setting or get_system_setting()
    return {
        "detail": "系统维护中，请稍后再试",
        "error_code": SYSTEM_MAINTENANCE_ERROR_CODE,
        "maintenance": build_maintenance_state_payload(target),
    }


def ensure_maintenance_activated(*, actor=None) -> SystemSetting:
    with transaction.atomic():
        setting = SystemSetting.objects.select_for_update().get(singleton_key="default")
        if not is_maintenance_active(setting):
            return setting
        now = timezone.now()
        update_fields: list[str] = []
        if setting.maintenance_activated_at is None:
            setting.maintenance_activated_at = now
            update_fields.append("maintenance_activated_at")
        if setting.maintenance_processed_at is None:
            _revoke_upload_merge_tasks()
            notify_all_non_superusers_force_logout("系统维护已开启，请稍后再试")
            notify_all_users_event(
                "system.settings.updated",
                {"system": build_system_settings_payload()},
                domain="system",
            )
            setting.maintenance_processed_at = now
            update_fields.append("maintenance_processed_at")
        if actor is not None and getattr(actor, "is_authenticated", False):
            setting.updated_by = actor
            update_fields.append("updated_by")
        if update_fields:
            update_fields.append("updated_at")
            setting.save(update_fields=update_fields)
        return setting


def update_system_setting(*, actor, system_title: str | None = None, maintenance_enabled: bool | None = None, maintenance_scheduled_at=None) -> dict[str, object]:
    should_publish_maintenance_schedule_announcement = False
    scheduled_announcement_message = ""
    with transaction.atomic():
        setting = SystemSetting.objects.select_for_update().get_or_create(singleton_key="default")[0]
        update_fields: list[str] = []
        previous_maintenance_enabled = bool(setting.maintenance_enabled)
        previous_maintenance_scheduled_at = setting.maintenance_scheduled_at

        if system_title is not None:
            normalized_title = str(system_title or "").strip()
            if setting.system_title != normalized_title:
                setting.system_title = normalized_title
                update_fields.append("system_title")

        if maintenance_enabled is not None:
            next_enabled = bool(maintenance_enabled)
            if setting.maintenance_enabled != next_enabled:
                setting.maintenance_enabled = next_enabled
                update_fields.append("maintenance_enabled")
            if next_enabled:
                if setting.maintenance_scheduled_at != maintenance_scheduled_at:
                    setting.maintenance_scheduled_at = maintenance_scheduled_at
                    update_fields.append("maintenance_scheduled_at")
                if maintenance_scheduled_at and maintenance_scheduled_at > timezone.now():
                    if setting.maintenance_activated_at is not None:
                        setting.maintenance_activated_at = None
                        update_fields.append("maintenance_activated_at")
                    if setting.maintenance_processed_at is not None:
                        setting.maintenance_processed_at = None
                        update_fields.append("maintenance_processed_at")
                else:
                    if setting.maintenance_activated_at is None:
                        setting.maintenance_activated_at = timezone.now()
                        update_fields.append("maintenance_activated_at")
                    if setting.maintenance_processed_at is not None:
                        setting.maintenance_processed_at = None
                        update_fields.append("maintenance_processed_at")
            else:
                if setting.maintenance_scheduled_at is not None:
                    setting.maintenance_scheduled_at = None
                    update_fields.append("maintenance_scheduled_at")
                if setting.maintenance_activated_at is not None:
                    setting.maintenance_activated_at = None
                    update_fields.append("maintenance_activated_at")
                if setting.maintenance_processed_at is not None:
                    setting.maintenance_processed_at = None
                    update_fields.append("maintenance_processed_at")

        setting.updated_by = actor
        update_fields.append("updated_by")

        if (
            maintenance_enabled is not None
            and bool(maintenance_enabled)
            and maintenance_scheduled_at is not None
            and maintenance_scheduled_at > timezone.now()
        ):
            schedule_changed = previous_maintenance_scheduled_at != maintenance_scheduled_at
            enabled_changed = not previous_maintenance_enabled
            if schedule_changed or enabled_changed:
                should_publish_maintenance_schedule_announcement = True
                scheduled_announcement_message = (
                    f"系统将于 {maintenance_scheduled_at.strftime('%Y-%m-%d %H:%M:%S')} 开启维护，"
                    "维护期间无法访问系统，请注意时间。"
                )

        if update_fields:
            setting.save(update_fields=[*dict.fromkeys([*update_fields, "updated_at"])])

        if should_publish_maintenance_schedule_announcement:
            transaction.on_commit(
                lambda: create_announcement(
                    actor=actor,
                    title="系统维护通知",
                    content=scheduled_announcement_message,
                )
            )

    if is_maintenance_active(setting):
        setting = ensure_maintenance_activated(actor=actor)
    else:
        notify_all_users_event(
            "system.settings.updated",
            {"system": build_system_settings_payload()},
            domain="system",
        )

    return build_system_settings_payload()


def serialize_announcement(announcement: SystemAnnouncement, *, user=None) -> dict[str, object]:
    read_at = None
    if user is not None and getattr(user, "is_authenticated", False):
        read_record = getattr(announcement, "current_user_read_record", None)
        if read_record is None:
            read_record = SystemAnnouncementRead.objects.filter(announcement=announcement, user=user).first()
        read_at = read_record.read_at if read_record else None
    return {
        "id": announcement.id,
        "title": announcement.title,
        "content": announcement.content,
        "published_at": announcement.published_at.isoformat() if announcement.published_at else None,
        "published_by": getattr(getattr(announcement, "published_by", None), "display_name", "")
        or getattr(getattr(announcement, "published_by", None), "username", ""),
        "is_read": read_at is not None,
        "read_at": read_at.isoformat() if read_at else None,
    }


def list_announcements_for_user(user) -> list[dict[str, object]]:
    announcements = list(
        SystemAnnouncement.objects.select_related("published_by").prefetch_related("read_records").all()[:50]
    )
    read_map = {
        record.announcement_id: record
        for record in SystemAnnouncementRead.objects.filter(user=user, announcement__in=announcements).select_related("announcement")
    }
    for announcement in announcements:
        announcement.current_user_read_record = read_map.get(announcement.id)
    return [serialize_announcement(item, user=user) for item in announcements]


def create_announcement(*, actor, title: str, content: str) -> dict[str, object]:
    normalized_title = str(title or "").strip()
    normalized_content = str(content or "").strip()
    if not normalized_title:
        raise ValueError("公告标题不能为空")
    if not normalized_content:
        raise ValueError("公告内容不能为空")
    if len(normalized_title) > 255:
        raise ValueError("公告标题不能超过255个字符")
    max_length = resolve_announcement_content_max_length()
    if len(normalized_content) > max_length:
        raise ValueError(f"公告内容不能超过{max_length}个字符")

    announcement = SystemAnnouncement.objects.create(
        title=normalized_title,
        content=normalized_content,
        published_by=actor,
    )
    payload = serialize_announcement(announcement, user=actor)
    notify_all_users_event(
        "system.announcement.created",
        {"announcement": payload},
        domain="system",
    )
    return payload


def mark_announcement_read(*, user, announcement_id: int) -> dict[str, object]:
    announcement = SystemAnnouncement.objects.filter(id=announcement_id).first()
    if announcement is None:
        raise ValueError("公告不存在")
    read_record, created = SystemAnnouncementRead.objects.get_or_create(
        announcement=announcement,
        user=user,
        defaults={"read_at": timezone.now()},
    )
    if not created:
        read_record.read_at = timezone.now()
        read_record.save(update_fields=["read_at"])
    announcement.current_user_read_record = read_record
    return serialize_announcement(announcement, user=user)


def mark_all_announcements_read(*, user) -> dict[str, int]:
    now = timezone.now()
    announcement_ids = list(SystemAnnouncement.objects.values_list("id", flat=True))
    existing_ids = set(SystemAnnouncementRead.objects.filter(user=user, announcement_id__in=announcement_ids).values_list("announcement_id", flat=True))
    new_records = [
        SystemAnnouncementRead(announcement_id=announcement_id, user=user, read_at=now)
        for announcement_id in announcement_ids
        if announcement_id not in existing_ids
    ]
    if new_records:
        SystemAnnouncementRead.objects.bulk_create(new_records)
    return {"updated_count": len(new_records)}


def delete_announcement(*, announcement_id: int) -> None:
    announcement = SystemAnnouncement.objects.filter(id=announcement_id).first()
    if announcement is None:
        raise ValueError("公告不存在")
    announcement.delete()
    notify_all_users_event(
        "system.announcement.deleted",
        {"announcement_id": announcement_id},
        domain="system",
    )


def _revoke_upload_merge_tasks() -> None:
    inspect = current_app.control.inspect(timeout=1)
    task_batches = []
    try:
        task_batches.extend(filter(None, [inspect.active(), inspect.reserved(), inspect.scheduled()]))
    except Exception:
        return

    target_names = {"hyself.tasks.merge_large_file_task"}
    revoked_ids: set[str] = set()
    for batch in task_batches:
        for tasks in batch.values():
            for task in tasks or []:
                task_name = str(task.get("name") or task.get("request", {}).get("name") or "")
                task_id = str(task.get("id") or task.get("request", {}).get("id") or "").strip()
                if task_name in target_names and task_id and task_id not in revoked_ids:
                    current_app.control.revoke(task_id, terminate=True)
                    revoked_ids.add(task_id)