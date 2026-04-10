from __future__ import annotations

from django.db import transaction

from bbot.models import AssetReference
from utils.upload import normalize_relative_path


@transaction.atomic
def upsert_asset_reference(*, lookup: dict, values: dict, manager=None) -> AssetReference:
    reference_manager = manager or AssetReference.all_objects
    reference = reference_manager.filter(**lookup).first()
    payload = {**lookup, **values}
    if reference is None:
        return reference_manager.create(**payload)

    update_fields: list[str] = []
    for field_name, value in payload.items():
        if getattr(reference, field_name) != value:
            setattr(reference, field_name, value)
            update_fields.append(field_name)
    if update_fields:
        reference.save(update_fields=[*update_fields, "updated_at"])
    return reference


@transaction.atomic
def create_chat_attachment_asset_reference(*, source_reference: AssetReference, owner_user, conversation_id: int) -> AssetReference:
    if source_reference.asset is None:
        raise ValueError("source_reference must be bound to an asset")

    return AssetReference.objects.create(
        asset=source_reference.asset,
        owner_user=owner_user,
        ref_domain=AssetReference.RefDomain.CHAT,
        ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
        ref_object_id=str(conversation_id),
        display_name=source_reference.display_name or source_reference.asset.original_name,
        relative_path_cache=normalize_relative_path(source_reference.relative_path_cache or source_reference.asset.storage_key),
        status=AssetReference.Status.ACTIVE,
        visibility=AssetReference.Visibility.CONVERSATION,
        extra_metadata={
            "source_asset_reference_id": source_reference.id,
            "source_ref_domain": source_reference.ref_domain,
            "source_ref_type": source_reference.ref_type,
        },
    )


@transaction.atomic
def upsert_resource_center_reference(*, entry, asset, parent_reference) -> AssetReference:
    normalized_path = normalize_relative_path(entry.relative_path)
    if entry.deleted_at is not None:
        status = AssetReference.Status.DELETED
    elif entry.recycled_at is not None:
        status = AssetReference.Status.RECYCLED
    else:
        status = AssetReference.Status.ACTIVE

    if entry.is_dir:
        ref_type = AssetReference.RefType.DIRECTORY
    elif str(normalized_path).startswith("avatars/") or entry.business == "profile":
        ref_type = AssetReference.RefType.AVATAR
    else:
        ref_type = AssetReference.RefType.FILE

    visibility = AssetReference.Visibility.SYSTEM if entry.is_system else AssetReference.Visibility.PRIVATE

    return upsert_asset_reference(
        lookup={"legacy_uploaded_file": entry},
        values={
            "asset": asset,
            "owner_user": entry.created_by,
            "ref_domain": AssetReference.RefDomain.RESOURCE_CENTER,
            "ref_type": ref_type,
            "ref_object_id": str(entry.id),
            "display_name": entry.display_name,
            "parent_reference": parent_reference,
            "relative_path_cache": normalized_path,
            "status": status,
            "recycled_at": entry.recycled_at,
            "visibility": visibility,
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


@transaction.atomic
def upsert_user_profile_avatar_reference(*, asset, user, display_name: str, relative_path: str) -> AssetReference:
    normalized_path = normalize_relative_path(relative_path)
    return upsert_asset_reference(
        lookup={
            "owner_user": user,
            "ref_domain": AssetReference.RefDomain.USER_PROFILE,
            "ref_type": AssetReference.RefType.AVATAR,
            "ref_object_id": str(user.id),
        },
        values={
            "asset": asset,
            "display_name": display_name,
            "parent_reference": None,
            "relative_path_cache": normalized_path,
            "status": AssetReference.Status.ACTIVE,
            "recycled_at": None,
            "visibility": AssetReference.Visibility.PRIVATE,
            "extra_metadata": {"profile_user_id": user.id, "source": "direct_avatar_upload"},
            "deleted_at": None,
            "legacy_uploaded_file": None,
        },
    )