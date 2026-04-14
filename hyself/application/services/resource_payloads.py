from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from hyself.asset_compat import ensure_asset_compat_for_uploaded_file, serialize_asset_payload, serialize_asset_reference_payload
from hyself.models import AssetReference, UploadedFile
from hyself.recycle_bin import RECYCLE_BIN_EXPIRE_DAYS, is_recycle_bin_folder
from hyself.utils.upload import media_url


def _remaining_days(expires_at):
    if not expires_at:
        return None
    return max(0, (expires_at.date() - timezone.now().date()).days)


def file_item_payload(item: UploadedFile, *, entry_is_within_recycle_bin_tree) -> dict:
    asset, asset_reference = ensure_asset_compat_for_uploaded_file(item)
    expires_at = item.recycled_at + timedelta(days=RECYCLE_BIN_EXPIRE_DAYS) if item.recycled_at else None
    return {
        "id": item.id,
        "display_name": item.display_name,
        "stored_name": item.stored_name,
        "resource_kind": "resource_center" if item.business != "chat" else "chat_upload",
        "owner_user_id": item.created_by_id,
        "virtual_path": None,
        "virtual_kind": None,
        "is_dir": item.is_dir,
        "parent_id": item.parent_id,
        "file_size": item.file_size,
        "file_md5": item.file_md5,
        "relative_path": item.relative_path,
        "url": "" if item.is_dir or not item.relative_path else media_url(item.relative_path),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "is_system": item.is_system,
        "is_recycle_bin": is_recycle_bin_folder(item),
        "in_recycle_bin_tree": entry_is_within_recycle_bin_tree(item),
        "recycled_at": item.recycled_at,
        "expires_at": expires_at,
        "remaining_days": _remaining_days(expires_at),
        "recycle_original_parent_id": item.recycle_original_parent_id,
        "owner_name": (item.created_by.display_name or item.created_by.username) if item.created_by is not None else "",
        "asset_reference_id": asset_reference.id,
        "asset_reference": serialize_asset_reference_payload(asset_reference),
        "asset": serialize_asset_payload(asset),
    }


def file_reference_payload(reference: AssetReference, *, entry_is_within_recycle_bin_tree) -> dict:
    legacy_item = reference.legacy_uploaded_file
    is_dir = reference.ref_type == AssetReference.RefType.DIRECTORY
    expires_at = reference.recycled_at + timedelta(days=RECYCLE_BIN_EXPIRE_DAYS) if reference.recycled_at else None

    relative_path = reference.relative_path_cache or (legacy_item.relative_path if legacy_item else "")
    display_name = reference.display_name or (legacy_item.display_name if legacy_item else "")
    parent_id = None
    if legacy_item is not None:
        parent_id = legacy_item.parent_id
    elif reference.parent_reference is not None:
        parent_id = reference.parent_reference.legacy_uploaded_file_id or reference.parent_reference_id

    return {
        "id": legacy_item.id if legacy_item is not None else reference.id,
        "display_name": display_name,
        "stored_name": legacy_item.stored_name if legacy_item is not None else Path(relative_path).name,
        "resource_kind": "resource_center",
        "owner_user_id": legacy_item.created_by_id if legacy_item is not None else reference.owner_user_id,
        "virtual_path": None,
        "virtual_kind": None,
        "is_virtual": False,
        "is_dir": is_dir,
        "parent_id": parent_id,
        "file_size": reference.asset.file_size if reference.asset is not None else (legacy_item.file_size if legacy_item is not None else 0),
        "file_md5": reference.asset.file_md5 or "" if reference.asset is not None else (legacy_item.file_md5 if legacy_item is not None else ""),
        "relative_path": relative_path,
        "url": "" if is_dir or not relative_path else (serialize_asset_payload(reference.asset) or {}).get("url", media_url(relative_path)),
        "created_at": legacy_item.created_at if legacy_item is not None else reference.created_at,
        "updated_at": legacy_item.updated_at if legacy_item is not None else reference.updated_at,
        "is_system": legacy_item.is_system if legacy_item is not None else reference.visibility == AssetReference.Visibility.SYSTEM,
        "is_recycle_bin": bool(legacy_item and is_recycle_bin_folder(legacy_item)),
        "in_recycle_bin_tree": bool(legacy_item and entry_is_within_recycle_bin_tree(legacy_item)),
        "recycled_at": reference.recycled_at,
        "expires_at": expires_at,
        "remaining_days": _remaining_days(expires_at),
        "recycle_original_parent_id": legacy_item.recycle_original_parent_id if legacy_item is not None else None,
        "owner_name": (
            (legacy_item.created_by.display_name or legacy_item.created_by.username)
            if legacy_item is not None and legacy_item.created_by is not None
            else ((reference.owner_user.display_name or reference.owner_user.username) if reference.owner_user is not None else "")
        ),
        "asset_reference_id": reference.id,
        "asset_reference": serialize_asset_reference_payload(reference),
        "asset": serialize_asset_payload(reference.asset),
    }


def build_system_search_entry_payload(entry: UploadedFile, *, entry_is_within_recycle_bin_tree) -> dict:
    owner_name = (entry.created_by.display_name or entry.created_by.username) if entry.created_by is not None else "未知用户"
    chain: list[str] = []
    cursor = entry.parent
    while cursor is not None:
        chain.append(cursor.display_name)
        cursor = cursor.parent
    chain.reverse()
    directory_path = "/".join(chain)
    full_path = f"{owner_name}/{directory_path}/{entry.display_name}" if directory_path else f"{owner_name}/{entry.display_name}"
    payload = file_item_payload(entry, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)
    payload["directory_path"] = directory_path
    payload["full_path"] = full_path
    return payload


def build_reference_search_payload(reference: AssetReference, *, directory_path: str, entry_is_within_recycle_bin_tree) -> dict:
    payload = file_reference_payload(reference, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)
    payload["directory_path"] = directory_path
    payload["full_path"] = f"{directory_path}/{reference.display_name}" if directory_path else reference.display_name
    return payload


def build_avatar_upload_payload(*, display_name: str, stored_name: str, relative_path: str, file_size: int, asset, asset_reference) -> dict:
    return {
        "mode": "direct",
        "file": {
            "id": 0,
            "display_name": display_name,
            "stored_name": stored_name,
            "is_dir": False,
            "parent_id": None,
            "file_size": file_size,
            "file_md5": "",
            "relative_path": relative_path,
            "url": media_url(relative_path),
            "created_at": None,
            "updated_at": None,
            "is_system": False,
            "is_recycle_bin": False,
            "recycled_at": None,
            "expires_at": None,
            "remaining_days": None,
            "recycle_original_parent_id": None,
            "asset_reference_id": asset_reference.id,
            "asset_reference": serialize_asset_reference_payload(asset_reference),
            "asset": serialize_asset_payload(asset),
        },
    }