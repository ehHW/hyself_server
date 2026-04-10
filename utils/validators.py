"""
验证和解析函数模块
"""
from pathlib import Path
from django.conf import settings


# 常量定义
ALLOWED_UPLOAD_CATEGORIES = {"", "profile", "chat"}
ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
AVATAR_MAX_SIZE = int(getattr(settings, "AVATAR_MAX_SIZE", 5 * 1024 * 1024))


def parse_parent_id(raw_parent_id) -> int | None:
    """
    解析父目录ID
    
    Args:
        raw_parent_id: 原始父目录ID（可能是字符串）
        
    Returns:
        解析后的父目录ID，或None
    """
    if raw_parent_id in [None, "", "null", "None"]:
        return None
    try:
        value = int(raw_parent_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_category(raw_category: str) -> str | None:
    """
    解析上传分类
    
    Args:
        raw_category: 原始分类字符串
        
    Returns:
        合法的分类名，或None
    """
    category = str(raw_category or "").strip().lower()
    if category in ALLOWED_UPLOAD_CATEGORIES:
        return category
    return None


def validate_avatar_upload_file(file_obj) -> str | None:
    """
    验证头像上传文件
    
    Args:
        file_obj: 上传的文件对象
        
    Returns:
        错误消息字符串，或None（表示验证通过）
    """
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
