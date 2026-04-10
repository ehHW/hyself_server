from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction

from bbot.application.services.asset_references import upsert_asset_reference, upsert_resource_center_reference, upsert_user_profile_avatar_reference
from bbot.models import Asset, AssetReference, UploadedFile
from utils.upload import media_url, normalize_relative_path


def detect_legacy_uploaded_file_media_type(entry: UploadedFile) -> str:
    relative_path = normalize_relative_path(entry.relative_path)
    if entry.business == "profile" or relative_path.startswith("avatars/"):
        return Asset.MediaType.AVATAR
    guessed_mime, _ = mimetypes.guess_type(entry.display_name or relative_path or entry.stored_name)
    guessed_mime = str(guessed_mime or "").lower()
    if guessed_mime.startswith("image/"):
        return Asset.MediaType.IMAGE
    if guessed_mime.startswith("audio/"):
        return Asset.MediaType.AUDIO
    if guessed_mime.startswith("video/"):
        return Asset.MediaType.VIDEO
    return Asset.MediaType.FILE


def build_asset_reference_status(entry: UploadedFile) -> str:
    if entry.deleted_at is not None:
        return AssetReference.Status.DELETED
    if entry.recycled_at is not None:
        return AssetReference.Status.RECYCLED
    return AssetReference.Status.ACTIVE


def build_asset_reference_domain(entry: UploadedFile) -> str:
    relative_path = normalize_relative_path(entry.relative_path)
    if entry.business == "profile" or relative_path.startswith("avatars/"):
        return AssetReference.RefDomain.USER_PROFILE
    if entry.business == "chat":
        return AssetReference.RefDomain.SYSTEM
    return AssetReference.RefDomain.RESOURCE_CENTER


def build_asset_reference_type(entry: UploadedFile) -> str:
    if entry.is_dir:
        return AssetReference.RefType.DIRECTORY
    if detect_legacy_uploaded_file_media_type(entry) == Asset.MediaType.AVATAR:
        return AssetReference.RefType.AVATAR
    return AssetReference.RefType.FILE


def build_asset_reference_visibility(entry: UploadedFile) -> str:
    if entry.is_system:
        return AssetReference.Visibility.SYSTEM
    return AssetReference.Visibility.PRIVATE


def serialize_asset_payload(asset: Asset | None) -> dict | None:
    if asset is None:
        return None

    return {
        "id": asset.id,
        "file_md5": asset.file_md5,
        "sha256": asset.sha256,
        "storage_backend": asset.storage_backend,
        "storage_key": asset.storage_key,
        "mime_type": asset.mime_type,
        "media_type": asset.media_type,
        "file_size": asset.file_size,
        "original_name": asset.original_name,
        "extension": asset.extension,
        "width": asset.width,
        "height": asset.height,
        "duration_seconds": asset.duration_seconds,
        "extra_metadata": asset.extra_metadata or {},
        "url": media_url(asset.storage_key) if asset.storage_key and asset.storage_backend == Asset.StorageBackend.LOCAL else "",
    }


def serialize_asset_reference_payload(reference: AssetReference | None) -> dict | None:
    if reference is None:
        return None

    return {
        "id": reference.id,
        "asset_id": reference.asset_id,
        "owner_user_id": reference.owner_user_id,
        "ref_domain": reference.ref_domain,
        "ref_type": reference.ref_type,
        "ref_object_id": reference.ref_object_id,
        "display_name": reference.display_name,
        "parent_reference_id": reference.parent_reference_id,
        "relative_path_cache": reference.relative_path_cache,
        "status": reference.status,
        "recycled_at": reference.recycled_at,
        "deleted_at": reference.deleted_at,
        "visibility": reference.visibility,
        "asset": serialize_asset_payload(reference.asset),
    }


@transaction.atomic
def ensure_asset_for_uploaded_file(entry: UploadedFile) -> Asset | None:
    if entry.is_dir:
        return None

    storage_key = normalize_relative_path(entry.relative_path)
    original_name = entry.display_name or entry.stored_name or Path(storage_key).name
    extension = Path(original_name).suffix.lower()
    media_type = detect_legacy_uploaded_file_media_type(entry)
    asset = None

    if entry.file_md5:
        asset = Asset.all_objects.filter(file_md5=entry.file_md5).first()
    if asset is None and storage_key:
        asset = Asset.all_objects.filter(storage_backend=Asset.StorageBackend.LOCAL, storage_key=storage_key).first()

    if asset is None:
        asset = Asset.all_objects.create(
            file_md5=entry.file_md5 or None,
            storage_backend=Asset.StorageBackend.LOCAL,
            storage_key=storage_key,
            mime_type=mimetypes.guess_type(original_name)[0] or "",
            media_type=media_type,
            file_size=int(entry.file_size or 0),
            original_name=original_name,
            extension=extension,
            created_by=entry.created_by,
            extra_metadata={
                "legacy_uploaded_file_id": entry.id,
                "legacy_business": entry.business,
                "legacy_stored_name": entry.stored_name,
            },
        )
        return asset

    update_fields: list[str] = []
    if asset.deleted_at is not None:
        asset.deleted_at = None
        update_fields.append("deleted_at")
    if not asset.storage_key and storage_key:
        asset.storage_key = storage_key
        update_fields.append("storage_key")
    if not asset.file_md5 and entry.file_md5:
        asset.file_md5 = entry.file_md5
        update_fields.append("file_md5")
    if asset.file_size != int(entry.file_size or 0):
        asset.file_size = int(entry.file_size or 0)
        update_fields.append("file_size")
    if not asset.original_name and original_name:
        asset.original_name = original_name
        update_fields.append("original_name")
    if not asset.extension and extension:
        asset.extension = extension
        update_fields.append("extension")
    if not asset.mime_type:
        asset.mime_type = mimetypes.guess_type(original_name)[0] or ""
        update_fields.append("mime_type")
    if asset.media_type != media_type and asset.media_type == Asset.MediaType.FILE:
        asset.media_type = media_type
        update_fields.append("media_type")
    if update_fields:
        asset.save(update_fields=[*update_fields, "updated_at"])
    return asset


@transaction.atomic
def ensure_asset_reference_for_uploaded_file(entry: UploadedFile) -> AssetReference:
    parent_reference = None
    if entry.parent_id:
        parent_reference = ensure_asset_reference_for_uploaded_file(entry.parent)
    asset = ensure_asset_for_uploaded_file(entry)
    domain = build_asset_reference_domain(entry)
    if domain == AssetReference.RefDomain.RESOURCE_CENTER:
        return upsert_resource_center_reference(
            entry=entry,
            asset=asset,
            parent_reference=parent_reference,
        )

    if domain == AssetReference.RefDomain.SYSTEM:
        return upsert_asset_reference(
            lookup={"legacy_uploaded_file": entry},
            values={
                "asset": asset,
                "owner_user": entry.created_by,
                "ref_domain": AssetReference.RefDomain.SYSTEM,
                "ref_type": build_asset_reference_type(entry),
                "ref_object_id": str(entry.id),
                "display_name": entry.display_name,
                "parent_reference": parent_reference,
                "relative_path_cache": normalize_relative_path(entry.relative_path),
                "status": build_asset_reference_status(entry),
                "recycled_at": entry.recycled_at,
                "visibility": AssetReference.Visibility.SYSTEM,
                "extra_metadata": {
                    "legacy_uploaded_file_id": entry.id,
                    "legacy_business": entry.business,
                    "legacy_stored_name": entry.stored_name,
                    "legacy_is_dir": entry.is_dir,
                    "legacy_is_system": entry.is_system,
                    "legacy_parent_id": entry.parent_id,
                },
                "deleted_at": entry.deleted_at,
            },
        )

    raise ValueError("UploadedFile profile references should not be synchronized through resource center compat flow")


def ensure_asset_compat_for_uploaded_file(entry: UploadedFile) -> tuple[Asset | None, AssetReference]:
    asset = ensure_asset_for_uploaded_file(entry)
    reference = ensure_asset_reference_for_uploaded_file(entry)
    return asset, reference


@transaction.atomic
def create_user_profile_asset_reference(*, user, display_name: str, relative_path: str, file_size: int = 0, file_md5: str | None = None) -> tuple[Asset, AssetReference]:
    normalized_path = normalize_relative_path(relative_path)
    original_name = display_name or Path(normalized_path).name
    extension = Path(original_name).suffix.lower()
    asset = Asset.all_objects.filter(storage_backend=Asset.StorageBackend.LOCAL, storage_key=normalized_path).first()
    if asset is None:
        asset = Asset.all_objects.create(
            file_md5=file_md5 or None,
            storage_backend=Asset.StorageBackend.LOCAL,
            storage_key=normalized_path,
            mime_type=mimetypes.guess_type(original_name)[0] or "",
            media_type=Asset.MediaType.AVATAR,
            file_size=int(file_size or 0),
            original_name=original_name,
            extension=extension,
            created_by=user,
            extra_metadata={"profile_user_id": user.id},
        )
    else:
        update_fields: list[str] = []
        if asset.deleted_at is not None:
            asset.deleted_at = None
            update_fields.append("deleted_at")
        if file_md5 and asset.file_md5 != file_md5:
            asset.file_md5 = file_md5
            update_fields.append("file_md5")
        if asset.file_size != int(file_size or 0):
            asset.file_size = int(file_size or 0)
            update_fields.append("file_size")
        if asset.original_name != original_name:
            asset.original_name = original_name
            update_fields.append("original_name")
        if asset.extension != extension:
            asset.extension = extension
            update_fields.append("extension")
        if asset.media_type != Asset.MediaType.AVATAR:
            asset.media_type = Asset.MediaType.AVATAR
            update_fields.append("media_type")
        if update_fields:
            asset.save(update_fields=[*update_fields, "updated_at"])

    reference = upsert_user_profile_avatar_reference(
        asset=asset,
        user=user,
        display_name=original_name,
        relative_path=normalized_path,
    )
    return asset, reference