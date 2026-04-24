from __future__ import annotations

from django.conf import settings

from hyself.system_runtime import build_system_settings_payload

from user.models import DEFAULT_USER_ROLE_NAME, Permission, Role


MENU_PERMISSION_RULES: dict[str, dict[str, object]] = {
    "home": {"permissions": (), "match": "all", "superuser_only": False},
    "file_manage": {"permissions": ("file.view_resource",), "match": "all", "superuser_only": False},
    "chat_center": {"permissions": ("chat.view_conversation",), "match": "all", "superuser_only": False},
    "access_control": {
        "permissions": ("user.view_user", "user.view_role", "user.view_permission"),
        "match": "any",
        "superuser_only": False,
    },
    "user_manage": {"permissions": ("user.view_user",), "match": "all", "superuser_only": False},
    "role_manage": {"permissions": ("user.view_role",), "match": "all", "superuser_only": False},
    "permission_manage": {"permissions": ("user.view_permission",), "match": "all", "superuser_only": False},
    "entertainment": {
        "permissions": ("game.view_leaderboard", "entertainment.view_music", "entertainment.view_video"),
        "match": "any",
        "superuser_only": False,
    },
    "entertainment_game": {"permissions": ("game.view_leaderboard",), "match": "all", "superuser_only": False},
    "entertainment_game_2048": {"permissions": ("game.view_leaderboard",), "match": "all", "superuser_only": False},
    "entertainment_music": {"permissions": ("entertainment.view_music",), "match": "all", "superuser_only": False},
    "entertainment_video": {"permissions": ("entertainment.view_video",), "match": "all", "superuser_only": False},
    "account_center": {"permissions": (), "match": "all", "superuser_only": False},
    "profile_center": {"permissions": (), "match": "all", "superuser_only": False},
    "settings": {"permissions": (), "match": "all", "superuser_only": False},
}


def ensure_default_user_role() -> Role:
    role = Role.all_objects.filter(name=DEFAULT_USER_ROLE_NAME).first()
    if role is None:
        role = Role.all_objects.create(
            name=DEFAULT_USER_ROLE_NAME,
            description="系统默认基础角色，确保用户至少具备一个角色归属",
        )
    elif role.deleted_at is not None:
        role.deleted_at = None
        role.save(update_fields=["deleted_at", "updated_at"])
    return role


def ensure_user_has_minimum_role(user) -> Role | None:
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return None
    if user.roles.exists():
        return None
    role = ensure_default_user_role()
    user.roles.add(role)
    return role


def resolve_user_permission_codes(user) -> list[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    ensure_user_has_minimum_role(user)

    queryset = Permission.objects.all() if getattr(user, "is_superuser", False) else Permission.objects.filter(roles__users=user).distinct()
    return list(queryset.order_by("code").values_list("code", flat=True))


def resolve_visible_menu_keys(user, permission_codes: list[str] | None = None) -> list[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    permission_set = set(permission_codes or resolve_user_permission_codes(user))
    visible_keys: list[str] = []

    for menu_key, rule in MENU_PERMISSION_RULES.items():
        if rule.get("superuser_only") and not getattr(user, "is_superuser", False):
            continue

        required_permissions = tuple(rule.get("permissions") or ())
        if not required_permissions:
            visible_keys.append(menu_key)
            continue

        match_mode = str(rule.get("match") or "all")
        if match_mode == "any":
            if permission_set.intersection(required_permissions):
                visible_keys.append(menu_key)
            continue

        if all(permission in permission_set for permission in required_permissions):
            visible_keys.append(menu_key)

    return visible_keys


def build_permission_context_payload(user) -> dict[str, list[str]]:
    permission_codes = resolve_user_permission_codes(user)
    return {
        "permission_codes": permission_codes,
        "visible_menu_keys": resolve_visible_menu_keys(user, permission_codes),
    }


def build_session_context_payload(user) -> dict[str, object]:
    from chat.application.queries import execute_get_chat_settings_query

    permission_payload = build_permission_context_payload(user)
    return {
        **permission_payload,
        "system": build_system_settings_payload(),
        "chat": execute_get_chat_settings_query(user),
    }