from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError

from hyself.asset_compat import ensure_asset_compat_for_uploaded_file
from hyself.application.payloads.resource_center import (
    build_reference_search_result_payload,
    build_resource_reference_payload,
    build_system_search_uploaded_file_payload,
    build_uploaded_file_payload,
)
from hyself.application.services.resource_center import (
    build_system_owner_folder_payload,
    build_user_breadcrumbs_from_entry,
    build_video_artifacts_root_payload,
    ensure_asset_refs_for_entries,
    entry_is_within_recycle_bin_tree,
    get_scoped_parent_dir,
    resolve_video_artifact_virtual_items,
)
from hyself.models import AssetReference, UploadedFile
from hyself.recycle_bin import is_recycle_bin_folder


User = get_user_model()


def _resolve_active_system_owner(owner_user_id: int | None):
    if owner_user_id is None:
        return None

    owner_user = User.objects.filter(id=owner_user_id, deleted_at__isnull=True).first()
    if owner_user is None:
        raise ValidationError({"detail": "目标用户不存在"})
    return owner_user


def build_scoped_file_entries_payload(
    *,
    user,
    system_scope: bool,
    parent_id: int | None,
    owner_user_id: int | None = None,
    virtual_path: str | None = None,
) -> dict:
    owner_user = _resolve_active_system_owner(owner_user_id) if system_scope else None

    if system_scope and virtual_path:
        try:
            return build_virtual_path_listing_payload(virtual_path)
        except FileNotFoundError as exc:
            raise ValidationError({"detail": "目录不存在"}) from exc

    if system_scope and parent_id is None and owner_user_id is None:
        return build_system_root_listing_payload()

    parent = get_scoped_parent_dir(owner_user or user, parent_id, system_scope=system_scope)
    if parent_id is not None and parent is None:
        raise ValidationError({"detail": "目录不存在"})

    if system_scope:
        return build_system_parent_listing_payload(parent, owner_user=owner_user)

    return build_user_parent_listing_payload(user, parent)


def build_scoped_search_payload(
    *,
    user,
    system_scope: bool,
    keyword: str,
    limit: int,
    owner_user_id: int | None = None,
) -> dict:
    if system_scope:
        return build_system_search_payload(keyword, limit, owner_user_id=owner_user_id)
    return build_user_search_payload(user, keyword, limit)


def build_system_root_listing_payload() -> dict:
    owners = list(
        User.objects.filter(uploaded_files__deleted_at__isnull=True)
        .distinct()
        .order_by("username", "id")
    )
    return {
        "parent": None,
        "breadcrumbs": [{"id": None, "name": "系统文件"}],
        "items": [build_video_artifacts_root_payload(), *[build_system_owner_folder_payload(owner) for owner in owners]],
        "owner_user": None,
    }


def build_virtual_path_listing_payload(virtual_path: str) -> dict:
    parent_payload, items, breadcrumbs = resolve_video_artifact_virtual_items(virtual_path)
    return {
        "parent": parent_payload,
        "breadcrumbs": breadcrumbs,
        "items": items,
        "owner_user": None,
    }


def build_system_parent_listing_payload(parent: UploadedFile | None, owner_user=None) -> dict:
    entry_queryset = UploadedFile.objects.select_related("created_by").filter(parent=parent, deleted_at__isnull=True)
    if owner_user is not None:
        entry_queryset = entry_queryset.filter(created_by=owner_user)
    entries = list(entry_queryset.order_by("is_dir", "display_name", "id"))
    entries.sort(key=lambda item: (not item.is_dir, item.display_name or "", item.id))

    breadcrumbs = [{"id": None, "name": "系统文件"}]
    if owner_user is not None:
        breadcrumbs.append({"id": None, "name": owner_user.display_name or owner_user.username, "owner_user_id": owner_user.id})
    if parent is not None:
        chain: list[UploadedFile] = []
        cursor = parent
        while cursor is not None:
            chain.append(cursor)
            cursor = cursor.parent
        for node in reversed(chain):
            breadcrumbs.append({"id": node.id, "name": node.display_name, "owner_user_id": owner_user.id if owner_user is not None else None})

    return {
        "parent": None if parent is None else build_uploaded_file_payload(parent, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
        "breadcrumbs": breadcrumbs,
        "items": [build_uploaded_file_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in entries],
        "owner_user": None if owner_user is None else {"id": owner_user.id, "name": owner_user.display_name or owner_user.username},
    }


def build_user_parent_listing_payload(user, parent: UploadedFile | None) -> dict:
    orphan_queryset = UploadedFile.objects.filter(parent=parent, deleted_at__isnull=True, asset_reference_compat__isnull=True)
    orphan_queryset = orphan_queryset.filter(created_by=user)
    orphan_entries = list(orphan_queryset)
    ensure_asset_refs_for_entries(orphan_entries)

    parent_reference = None if parent is None else ensure_asset_compat_for_uploaded_file(parent)[1]
    if parent is not None and is_recycle_bin_folder(parent):
        recycle_items = list(
            UploadedFile.objects.select_related("created_by")
            .filter(created_by=user, parent=parent, deleted_at__isnull=True)
            .order_by("recycled_at", "display_name", "id")
        )
        return {
            "parent": build_uploaded_file_payload(parent, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
            "breadcrumbs": build_user_breadcrumbs_from_entry(parent),
            "items": [build_uploaded_file_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in recycle_items],
            "owner_user": None,
        }

    reference_items = list(
        AssetReference.objects.select_related("asset", "owner_user", "legacy_uploaded_file", "legacy_uploaded_file__created_by", "parent_reference", "parent_reference__legacy_uploaded_file")
        .filter(
            parent_reference=parent_reference,
            deleted_at__isnull=True,
            status=AssetReference.Status.ACTIVE,
            owner_user_id=user.id,
            ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
        )
    )
    reference_items.sort(key=lambda item: (item.ref_type != AssetReference.RefType.DIRECTORY, item.display_name or "", item.id))

    breadcrumbs = [{"id": None, "name": "我的文件"}]
    if parent_reference:
        chain: list[AssetReference] = []
        cursor = parent_reference
        while cursor:
            chain.append(cursor)
            cursor = cursor.parent_reference
        for node in reversed(chain):
            breadcrumbs.append({"id": node.legacy_uploaded_file_id or node.id, "name": node.display_name, "owner_user_id": None})

    return {
        "parent": None if not parent_reference else build_resource_reference_payload(parent_reference, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
        "breadcrumbs": breadcrumbs,
        "items": [build_resource_reference_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in reference_items],
        "owner_user": None,
    }


def build_system_search_payload(keyword: str, limit: int, owner_user_id: int | None = None) -> dict:
    entry_queryset = UploadedFile.objects.select_related("created_by", "parent").filter(display_name__icontains=keyword, deleted_at__isnull=True)
    if owner_user_id is not None:
        entry_queryset = entry_queryset.filter(created_by_id=owner_user_id)
    matched_entries = list(entry_queryset.order_by("display_name", "id")[:limit])
    return {
        "items": [build_system_search_uploaded_file_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in matched_entries]
    }


def _build_directory_path_map(reference_queryset) -> dict[int, str]:
    all_dirs = reference_queryset.values("id", "parent_reference_id", "display_name")
    dir_map = {
        int(item["id"]): {
            "parent_id": item["parent_reference_id"],
            "display_name": item["display_name"],
        }
        for item in all_dirs
    }
    path_cache: dict[int, str] = {}

    def build_dir_path(dir_id: int | None) -> str:
        if dir_id is None:
            return ""
        if dir_id in path_cache:
            return path_cache[dir_id]

        cursor = dir_id
        chain: list[str] = []
        visited: set[int] = set()
        while cursor and cursor in dir_map and cursor not in visited:
            visited.add(cursor)
            node = dir_map[cursor]
            chain.append(str(node["display_name"]))
            parent_id = node["parent_id"]
            cursor = int(parent_id) if parent_id else None

        chain.reverse()
        path = "/".join(chain)
        path_cache[dir_id] = path
        return path

    return {dir_id: build_dir_path(dir_id) for dir_id in dir_map}


def build_user_search_payload(user, keyword: str, limit: int) -> dict:
    orphan_queryset = UploadedFile.objects.filter(
        is_dir=False,
        display_name__icontains=keyword,
        deleted_at__isnull=True,
        asset_reference_compat__isnull=True,
        created_by=user,
    )
    orphan_entries = list(orphan_queryset[:limit])
    ensure_asset_refs_for_entries(orphan_entries)

    matched_files = list(
        AssetReference.objects.select_related("asset", "owner_user", "legacy_uploaded_file", "legacy_uploaded_file__created_by", "parent_reference", "parent_reference__legacy_uploaded_file")
        .filter(
            ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
            ref_type__in=[AssetReference.RefType.FILE, AssetReference.RefType.AVATAR],
            display_name__icontains=keyword,
            deleted_at__isnull=True,
            status=AssetReference.Status.ACTIVE,
            owner_user_id=user.id,
        )
        .order_by("display_name", "id")[:limit]
    )

    directory_paths = _build_directory_path_map(
        AssetReference.objects.filter(
            ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
            ref_type=AssetReference.RefType.DIRECTORY,
            deleted_at__isnull=True,
            status=AssetReference.Status.ACTIVE,
            owner_user=user,
        )
    )

    items = []
    for file_item in matched_files:
        items.append(
            build_reference_search_result_payload(
                file_item,
                directory_path=directory_paths.get(file_item.parent_reference_id, ""),
                entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree,
            )
        )

    return {"items": items}
