from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from hyself.asset_compat import ensure_asset_compat_for_uploaded_file, serialize_asset_payload
from hyself.models import Asset, AssetReference, UploadedFile
from hyself.recycle_bin import RECYCLE_BIN_DISPLAY_NAME, RECYCLE_BIN_STORED_NAME, RECYCLE_BIN_EXPIRE_DAYS, is_recycle_bin_folder
from hyself.utils.upload import (
    build_stored_name,
    get_upload_root,
    get_user_relative_root,
    get_user_upload_root,
    join_relative_path,
    media_url,
    normalize_relative_path,
    relative_to_uploads,
)


User = get_user_model()
VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH = "__video_artifacts__"
VIDEO_ARTIFACTS_ROOT_DISPLAY_NAME = "视频切片目录"
VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH = "video_artifacts"


def get_parent_dir(user, parent_id: int | None):
    if parent_id is None:
        return None
    return UploadedFile.objects.filter(id=parent_id, created_by=user, is_dir=True).first()


def get_scoped_parent_dir(user, parent_id: int | None, *, system_scope: bool = False):
    if not system_scope:
        return get_parent_dir(user, parent_id)
    if parent_id is None or not user.is_superuser:
        return None
    return UploadedFile.objects.filter(id=parent_id, is_dir=True, deleted_at__isnull=True).first()


def is_system_scope_request(request) -> bool:
    raw_scope = request.query_params.get("scope") if request.method == "GET" else request.data.get("scope")
    return str(raw_scope or "").strip().lower() == "system"


def split_relative_upload_path(raw_relative_path: str, fallback_name: str) -> tuple[list[str], str]:
    normalized = normalize_relative_path(raw_relative_path)
    if not normalized:
        return [], fallback_name
    segments = [item for item in normalized.split("/") if item]
    if not segments:
        return [], fallback_name
    file_name = segments[-1] or fallback_name
    return segments[:-1], file_name


def is_reserved_system_folder_name(name: str) -> bool:
    normalized = str(name).strip()
    return normalized in {RECYCLE_BIN_DISPLAY_NAME, RECYCLE_BIN_STORED_NAME}


def ensure_child_folder(user, parent: UploadedFile | None, folder_name: str) -> UploadedFile:
    if is_reserved_system_folder_name(folder_name):
        raise ValidationError({"detail": '“回收站”是系统保留目录名称，请使用其他名称'})

    exists = UploadedFile.objects.filter(
        created_by=user,
        parent=parent,
        is_dir=True,
        display_name=folder_name,
    ).first()
    if exists:
        return exists

    if parent and parent.relative_path:
        dir_relative_path = join_relative_path(parent.relative_path, folder_name)
    else:
        dir_relative_path = join_relative_path(get_user_relative_root(user), folder_name)

    (get_upload_root() / Path(dir_relative_path)).mkdir(parents=True, exist_ok=True)
    deleted_folder = UploadedFile.all_objects.filter(
        created_by=user,
        parent=parent,
        is_dir=True,
        display_name=folder_name,
        deleted_at__isnull=False,
    ).first()
    if deleted_folder:
        deleted_folder.deleted_at = None
        deleted_folder.relative_path = dir_relative_path
        deleted_folder.file_size = 0
        deleted_folder.file_md5 = ""
        deleted_folder.stored_name = folder_name
        deleted_folder.save(update_fields=["deleted_at", "relative_path", "file_size", "file_md5", "stored_name", "updated_at"])
        return deleted_folder

    return UploadedFile.objects.create(
        created_by=user,
        parent=parent,
        is_dir=True,
        display_name=folder_name,
        stored_name=folder_name,
        relative_path=dir_relative_path,
        file_size=0,
        file_md5="",
    )


def ensure_nested_parent(user, base_parent: UploadedFile | None, folders: list[str]) -> UploadedFile | None:
    current = base_parent
    for folder_name in folders:
        current = ensure_child_folder(user, current, folder_name)
    return current


def get_active_target_file(user, target_parent: UploadedFile | None, display_name: str) -> UploadedFile | None:
    return UploadedFile.objects.filter(
        created_by=user,
        parent=target_parent,
        display_name=display_name,
        is_dir=False,
        recycled_at__isnull=True,
    ).first()


def resolve_target_file_storage(
    user,
    target_parent: UploadedFile | None,
    display_name: str,
    preferred_stored_name: str,
    current_relative_path: str | None = None,
) -> tuple[str, Path, str]:
    target_dir = get_user_upload_root(user) if not target_parent else (get_upload_root() / Path(target_parent.relative_path))
    target_dir.mkdir(parents=True, exist_ok=True)

    stored_name = preferred_stored_name or build_stored_name(display_name)
    target_path = target_dir / stored_name
    target_relative_path = relative_to_uploads(target_path)

    if target_path.exists() and target_relative_path != (current_relative_path or ""):
        stored_name = build_stored_name(display_name)
        target_path = target_dir / stored_name
        target_relative_path = relative_to_uploads(target_path)

    return stored_name, target_path, target_relative_path


def relocate_recycled_file_to_target(
    user,
    existing: UploadedFile,
    target_parent: UploadedFile | None,
    display_name: str,
) -> None:
    current_relative_path = str(existing.relative_path or "")
    stored_name, target_path, target_relative_path = resolve_target_file_storage(
        user,
        target_parent,
        display_name,
        existing.stored_name,
        current_relative_path=current_relative_path,
    )

    if target_relative_path == current_relative_path:
        existing.stored_name = stored_name
        existing.relative_path = target_relative_path
        return

    source_path = get_upload_root() / Path(current_relative_path)
    if not source_path.exists() or not source_path.is_file():
        raise ValidationError({"detail": "回收站中的源文件不存在，无法恢复到目标目录"})

    has_other_active_refs = UploadedFile.objects.filter(
        relative_path=current_relative_path,
        is_dir=False,
        deleted_at__isnull=True,
        recycled_at__isnull=True,
    ).exclude(id=existing.id).exists()

    if has_other_active_refs:
        shutil.copy2(source_path, target_path)
    else:
        shutil.move(str(source_path), str(target_path))

    existing.stored_name = stored_name
    existing.relative_path = target_relative_path


def materialize_existing_upload_file(
    user,
    existing: UploadedFile,
    target_parent: UploadedFile | None,
    display_name: str,
    *,
    business: str = "",
) -> tuple[UploadedFile, bool]:
    target_existing = get_active_target_file(user, target_parent, display_name)
    if target_existing:
        if target_existing.file_md5 == existing.file_md5:
            if target_existing.business != business:
                target_existing.business = business
                target_existing.save(update_fields=["business", "updated_at"])
            return target_existing, False
        raise ValidationError({"detail": "目标目录已存在同名文件，但内容不同，请先重命名或删除原文件"})

    if existing.recycled_at is not None:
        relocate_recycled_file_to_target(user, existing, target_parent, display_name)
        existing.parent = target_parent
        existing.display_name = display_name
        existing.business = business
        existing.recycled_at = None
        existing.recycle_original_parent = None
        existing.save(update_fields=["parent", "display_name", "stored_name", "relative_path", "business", "recycled_at", "recycle_original_parent", "updated_at"])
        return existing, True

    if existing.created_by_id == user.id and existing.parent_id == (target_parent.id if target_parent else None) and existing.display_name == display_name:
        if existing.business != business:
            existing.business = business
            existing.save(update_fields=["business", "updated_at"])
        return existing, False

    duplicated = UploadedFile.objects.create(
        created_by=user,
        parent=target_parent,
        is_dir=False,
        display_name=display_name,
        stored_name=existing.stored_name,
        business=business,
        file_md5=existing.file_md5,
        file_size=existing.file_size,
        relative_path=existing.relative_path,
    )
    return duplicated, False


def resolve_existing_upload_file(
    user,
    file_md5: str,
    target_parent: UploadedFile | None,
    display_name: str,
    *,
    relative_path: str = "",
    business: str = "",
) -> tuple[UploadedFile | None, bool]:
    normalized_relative_path = normalize_relative_path(relative_path)
    existing = None

    if file_md5:
        existing = UploadedFile.objects.filter(
            created_by=user,
            file_md5=file_md5,
            is_dir=False,
            recycled_at__isnull=True,
        ).first()
        if not existing:
            existing = UploadedFile.objects.filter(
                created_by=user,
                file_md5=file_md5,
                is_dir=False,
                recycled_at__isnull=False,
            ).order_by("-recycled_at", "-id").first()

        if not existing:
            existing = UploadedFile.objects.filter(
                file_md5=file_md5,
                is_dir=False,
                deleted_at__isnull=True,
                recycled_at__isnull=True,
            ).exclude(created_by=user).exclude(relative_path="").order_by("-updated_at", "-id").first()

    if not existing and normalized_relative_path:
        existing = UploadedFile.objects.filter(
            created_by=user,
            relative_path=normalized_relative_path,
            is_dir=False,
            recycled_at__isnull=True,
        ).first()
        if not existing:
            existing = UploadedFile.objects.filter(
                created_by=user,
                relative_path=normalized_relative_path,
                is_dir=False,
                recycled_at__isnull=False,
            ).order_by("-recycled_at", "-id").first()

    if not existing:
        return None, False

    return materialize_existing_upload_file(user, existing, target_parent, display_name, business=business)


def entry_is_within_recycle_bin_tree(entry: UploadedFile | None) -> bool:
    cursor = entry
    while cursor is not None:
        if is_recycle_bin_folder(cursor):
            return True
        cursor = cursor.parent
    return False


def build_video_artifacts_root_payload() -> dict:
    now = timezone.now()
    return {
        "id": -1000001,
        "display_name": VIDEO_ARTIFACTS_ROOT_DISPLAY_NAME,
        "stored_name": VIDEO_ARTIFACTS_ROOT_DISPLAY_NAME,
        "resource_kind": "resource_center",
        "owner_user_id": None,
        "virtual_path": VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH,
        "virtual_kind": "video_artifacts_root",
        "owner_name": "",
        "is_virtual": True,
        "is_dir": True,
        "parent_id": None,
        "file_size": 0,
        "file_md5": "",
        "relative_path": VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH,
        "url": "",
        "created_at": now,
        "updated_at": now,
        "is_system": True,
        "is_recycle_bin": False,
        "in_recycle_bin_tree": False,
        "recycled_at": None,
        "expires_at": None,
        "remaining_days": None,
        "recycle_original_parent_id": None,
        "asset_reference_id": None,
        "asset_reference": None,
        "asset": None,
    }


def _video_artifact_asset_map() -> dict[str, Asset]:
    assets = Asset.objects.select_related("created_by").filter(media_type=Asset.MediaType.VIDEO, deleted_at__isnull=True).order_by("-updated_at", "-id")
    folder_map: dict[str, Asset] = {}
    for asset in assets:
        video_processing = ((asset.extra_metadata or {}).get("video_processing") if isinstance(asset.extra_metadata, dict) else None) or {}
        artifact_directory_path = str(video_processing.get("artifact_directory_path") or "").strip()
        if not artifact_directory_path.startswith(f"{VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH}/"):
            continue
        folder_name = Path(artifact_directory_path).name
        if folder_name and folder_name not in folder_map:
            folder_map[folder_name] = asset
    return folder_map


def _video_artifact_display_name(asset: Asset | None, folder_name: str) -> str:
    if asset is None:
        return folder_name
    return Path(str(asset.original_name or folder_name)).stem or folder_name


def build_video_artifact_directory_payload(folder_name: str, asset: Asset | None = None) -> dict:
    target_path = get_upload_root() / VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH / folder_name
    updated_at = timezone.now()
    if target_path.exists():
        updated_at = datetime.fromtimestamp(target_path.stat().st_mtime, tz=timezone.get_current_timezone())
    display_name = _video_artifact_display_name(asset, folder_name)
    return {
        "id": -2000000 - (asset.id if asset is not None else abs(hash(folder_name)) % 1000000),
        "display_name": display_name,
        "stored_name": folder_name,
        "resource_kind": "resource_center",
        "owner_user_id": asset.created_by_id if asset is not None else None,
        "virtual_path": f"{VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH}/{folder_name}",
        "virtual_kind": "video_artifact_directory",
        "owner_name": (asset.created_by.display_name or asset.created_by.username) if asset is not None and asset.created_by is not None else "",
        "is_virtual": True,
        "is_dir": True,
        "parent_id": None,
        "file_size": 0,
        "file_md5": "",
        "relative_path": f"{VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH}/{folder_name}",
        "url": "",
        "created_at": updated_at,
        "updated_at": updated_at,
        "is_system": True,
        "is_recycle_bin": False,
        "in_recycle_bin_tree": False,
        "recycled_at": None,
        "expires_at": None,
        "remaining_days": None,
        "recycle_original_parent_id": None,
        "asset_reference_id": None,
        "asset_reference": None,
        "asset": serialize_asset_payload(asset) if asset is not None else None,
    }


def build_video_artifact_file_payload(target_path: Path, *, virtual_path: str) -> dict:
    stat_result = target_path.stat()
    modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.get_current_timezone())
    relative_path = target_path.relative_to(get_upload_root()).as_posix()
    return {
        "id": -3000000 - (abs(hash(relative_path)) % 1000000),
        "display_name": target_path.name,
        "stored_name": target_path.name,
        "resource_kind": "resource_center",
        "owner_user_id": None,
        "virtual_path": virtual_path,
        "virtual_kind": "video_artifact_file",
        "owner_name": "",
        "is_virtual": True,
        "is_dir": target_path.is_dir(),
        "parent_id": None,
        "file_size": 0 if target_path.is_dir() else stat_result.st_size,
        "file_md5": "",
        "relative_path": relative_path,
        "url": "" if target_path.is_dir() else media_url(relative_path),
        "created_at": modified_at,
        "updated_at": modified_at,
        "is_system": True,
        "is_recycle_bin": False,
        "in_recycle_bin_tree": False,
        "recycled_at": None,
        "expires_at": None,
        "remaining_days": None,
        "recycle_original_parent_id": None,
        "asset_reference_id": None,
        "asset_reference": None,
        "asset": None,
    }


def resolve_video_artifact_virtual_items(virtual_path: str) -> tuple[dict | None, list[dict], list[dict]]:
    asset_map = _video_artifact_asset_map()
    root_payload = build_video_artifacts_root_payload()
    if virtual_path == VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH:
        items = [
            build_video_artifact_directory_payload(folder_name, asset_map.get(folder_name))
            for folder_name in sorted(asset_map.keys(), key=lambda item: _video_artifact_display_name(asset_map.get(item), item))
        ]
        breadcrumbs = [
            {"id": None, "name": "系统文件"},
            {"id": None, "name": VIDEO_ARTIFACTS_ROOT_DISPLAY_NAME, "virtual_path": VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH},
        ]
        return root_payload, items, breadcrumbs

    if not virtual_path.startswith(f"{VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH}/"):
        raise FileNotFoundError("虚拟目录不存在")
    folder_name = re.sub(r"/+$", "", virtual_path.split("/", 1)[1])
    if not folder_name:
        raise FileNotFoundError("虚拟目录不存在")

    target_dir = get_upload_root() / VIDEO_ARTIFACTS_ROOT_RELATIVE_PATH / folder_name
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError("虚拟目录不存在")

    asset = asset_map.get(folder_name)
    parent_payload = build_video_artifact_directory_payload(folder_name, asset)
    items = [
        build_video_artifact_file_payload(child, virtual_path=f"{virtual_path}/{child.name}")
        for child in sorted(target_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    ]
    breadcrumbs = [
        {"id": None, "name": "系统文件"},
        {"id": None, "name": VIDEO_ARTIFACTS_ROOT_DISPLAY_NAME, "virtual_path": VIDEO_ARTIFACTS_ROOT_VIRTUAL_PATH},
        {"id": None, "name": parent_payload["display_name"], "virtual_path": virtual_path},
    ]
    return parent_payload, items, breadcrumbs


def ensure_asset_refs_for_entries(entries: list[UploadedFile]) -> None:
    for entry in entries:
        if getattr(entry, "asset_reference_compat", None) is None:
            ensure_asset_compat_for_uploaded_file(entry)


def build_user_breadcrumbs_from_entry(parent: UploadedFile | None) -> list[dict]:
    breadcrumbs = [{"id": None, "name": "我的文件"}]
    if parent is None:
        return breadcrumbs
    chain: list[UploadedFile] = []
    cursor = parent
    while cursor is not None:
        chain.append(cursor)
        cursor = cursor.parent
    for node in reversed(chain):
        breadcrumbs.append({"id": node.id, "name": node.display_name, "owner_user_id": None})
    return breadcrumbs


def build_system_owner_folder_payload(owner) -> dict:
    display_name = owner.display_name or owner.username
    return {
        "id": -int(owner.id),
        "display_name": display_name,
        "stored_name": display_name,
        "resource_kind": "resource_center",
        "owner_user_id": owner.id,
        "virtual_path": None,
        "virtual_kind": None,
        "owner_name": display_name,
        "is_virtual": True,
        "is_dir": True,
        "parent_id": None,
        "file_size": 0,
        "file_md5": "",
        "relative_path": "",
        "url": "",
        "created_at": owner.date_joined,
        "updated_at": owner.date_joined,
        "is_system": True,
        "is_recycle_bin": False,
        "in_recycle_bin_tree": False,
        "recycled_at": None,
        "expires_at": None,
        "remaining_days": None,
        "recycle_original_parent_id": None,
        "asset_reference_id": None,
        "asset_reference": None,
        "asset": None,
    }