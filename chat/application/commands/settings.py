from __future__ import annotations

from chat.domain.preferences import get_or_create_user_preference


def execute_update_chat_settings_command(user, *, data: dict) -> dict:
    preference = get_or_create_user_preference(user)
    update_fields: list[str] = []
    if "theme_mode" in data:
        preference.theme_mode = "dark" if data["theme_mode"] == "dark" else "light"
        update_fields.append("theme_mode")
    if "chat_receive_notification" in data:
        preference.chat_receive_notification = bool(data["chat_receive_notification"])
        update_fields.append("chat_receive_notification")
    if "chat_list_sort_mode" in data:
        preference.chat_list_sort_mode = str(data["chat_list_sort_mode"])
        update_fields.append("chat_list_sort_mode")
    if "chat_stealth_inspect_enabled" in data:
        preference.chat_stealth_inspect_enabled = bool(data["chat_stealth_inspect_enabled"])
        update_fields.append("chat_stealth_inspect_enabled")
    if "settings_json" in data:
        preference.settings_json = data["settings_json"] or {}
        update_fields.append("settings_json")
    if update_fields:
        preference.save(update_fields=[*update_fields, "updated_at"])
    return {
        "theme_mode": "dark" if preference.theme_mode == "dark" else "light",
        "chat_receive_notification": bool(preference.chat_receive_notification),
        "chat_list_sort_mode": preference.chat_list_sort_mode,
        "chat_stealth_inspect_enabled": bool(preference.chat_stealth_inspect_enabled),
        "settings_json": preference.settings_json or {},
    }