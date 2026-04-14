from __future__ import annotations

from pathlib import Path

from django.conf import settings

from hyself.utils.upload import normalize_relative_path
from validators import parse_optional_positive_int


ALLOWED_UPLOAD_CATEGORIES = {"", "profile", "chat"}
ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
AVATAR_MAX_SIZE = int(getattr(settings, "AVATAR_MAX_SIZE", 5 * 1024 * 1024))


def parse_parent_id(raw_parent_id) -> int | None:
    return parse_optional_positive_int(raw_parent_id)


def parse_owner_user_id(raw_value) -> int | None:
    return parse_optional_positive_int(raw_value)


def parse_category(raw_category: str) -> str | None:
    category = str(raw_category or "").strip().lower()
    if category in ALLOWED_UPLOAD_CATEGORIES:
        return category
    return None


def parse_virtual_path(raw_value) -> str | None:
    value = normalize_relative_path(str(raw_value or "")).strip()
    return value or None


def validate_avatar_upload_file(file_obj) -> str | None:
    if int(getattr(file_obj, "size", 0) or 0) <= 0:
        return "头像文件不能为空"

    content_type = str(getattr(file_obj, "content_type", "") or "").lower()
    if not content_type.startswith("image/"):
        return "头像仅支持图片文件"

    suffix = Path(str(getattr(file_obj, "name", "") or "")).suffix.lower()
    if suffix not in ALLOWED_AVATAR_EXTENSIONS:
        return "头像格式不支持，仅支持 jpg/jpeg/png/webp/gif/bmp"

    if int(file_obj.size) > AVATAR_MAX_SIZE:
        return f"头像文件不能超过 {AVATAR_MAX_SIZE // 1024 // 1024}MB"

    return None