from __future__ import annotations

from chat.models import ChatConversationMember


def get_member_preferences(member: ChatConversationMember | None) -> dict:
    settings = member.extra_settings if member else {}
    return {
        "mute_notifications": bool((settings or {}).get("mute_notifications", False)),
        "group_nickname": str((settings or {}).get("group_nickname", "") or ""),
    }


def update_member_preferences(member: ChatConversationMember, *, mute_notifications: bool | None = None, group_nickname: str | None = None) -> ChatConversationMember:
    settings = dict(member.extra_settings or {})
    if mute_notifications is not None:
        settings["mute_notifications"] = bool(mute_notifications)
    if group_nickname is not None:
        settings["group_nickname"] = str(group_nickname)
    member.extra_settings = settings
    member.save(update_fields=["extra_settings", "updated_at"])
    return member