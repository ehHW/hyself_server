from __future__ import annotations


def user_brief(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "avatar": user.avatar,
    }


def to_serializable_datetime(value):
    if value is None:
        return None
    return value.isoformat()