import hashlib
import random
import re
import time
import uuid
from pathlib import Path

from django.conf import settings


def get_upload_root() -> Path:
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_temp_root() -> Path:
    root = get_upload_root() / "temp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_path_name(name: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5._-]+", "_", (name or "").strip())
    return normalized.strip("._") or "user"


def get_user_relative_root(user) -> str:
    users_root = Path("users")
    user_dir_name = f"{_sanitize_path_name(user.username)}_{user.id}"
    return (users_root / user_dir_name).as_posix()


def get_user_upload_root(user) -> Path:
    target = get_upload_root() / Path(get_user_relative_root(user))
    target.mkdir(parents=True, exist_ok=True)
    from bbot.recycle_bin import ensure_user_recycle_bin

    ensure_user_recycle_bin(user)
    return target


def get_avatar_relative_root(user) -> str:
    avatars_root = Path("avatars")
    user_dir_name = f"{_sanitize_path_name(user.username)}_{user.id}"
    return (avatars_root / user_dir_name).as_posix()


def get_avatar_upload_root(user) -> Path:
    target = get_upload_root() / Path(get_avatar_relative_root(user))
    target.mkdir(parents=True, exist_ok=True)
    return target


def normalize_relative_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    parts = [part for part in raw.split("/") if part and part not in {".", ".."}]
    return "/".join(parts)


def build_stored_name(original_name: str) -> str:
    ext = Path(original_name).suffix
    return f"{random.randint(100000, 999999)}_{uuid.uuid4().hex}_{int(time.time() * 1000)}{ext}"


def calc_file_md5(file_path: Path) -> str:
    digest = hashlib.md5()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calc_uploaded_file_md5(uploaded_file) -> str:
    digest = hashlib.md5()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def calc_path_md5(file_path: Path) -> str:
    digest = hashlib.md5()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_chunk_md5(chunk_path: Path, expected_md5: str) -> bool:
    return calc_path_md5(chunk_path) == expected_md5


def join_relative_path(*parts: str) -> str:
    normalized_parts = [normalize_relative_path(item) for item in parts if normalize_relative_path(item)]
    return "/".join(normalized_parts)


def relative_to_uploads(file_path: Path) -> str:
    return file_path.relative_to(get_upload_root()).as_posix()


def media_url(relative_path: str) -> str:
    return f"{settings.MEDIA_URL.rstrip('/')}/{relative_path}"
