from datetime import timedelta
from pathlib import Path
import shutil

from auth.permissions import AuthenticatedPermission as IsAuthenticated
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from bbot.asset_compat import (
    create_user_profile_asset_reference,
    ensure_asset_compat_for_uploaded_file,
    serialize_asset_payload,
    serialize_asset_reference_payload,
)
from bbot.models import Asset, AssetReference, UploadedFile
from bbot.video_processing import ensure_video_asset_pipeline
from bbot.recycle_bin import (
    RECYCLE_BIN_DISPLAY_NAME,
    RECYCLE_BIN_EXPIRE_DAYS,
    RECYCLE_BIN_STORED_NAME,
    clear_recycle_bin,
    ensure_user_recycle_bin,
    is_recycle_bin_folder,
    list_recycle_bin_entries,
    move_entry_to_recycle_bin,
    restore_entry_from_recycle_bin,
)
from bbot_server.celery import app as celery_app
from bbot.tasks import merge_large_file_task
from chat.domain.access import get_conversation_access
from chat.models import ChatConversation
from utils.upload import (
    build_stored_name,
    calc_uploaded_file_md5,
    get_avatar_upload_root,
    get_temp_root,
    get_upload_root,
    get_user_relative_root,
    get_user_upload_root,
    join_relative_path,
    media_url,
    normalize_relative_path,
    relative_to_uploads,
    verify_chunk_md5,
)
from utils.validators import parse_parent_id, parse_category, validate_avatar_upload_file


User = get_user_model()


def index(request):
    return JsonResponse({"data": "你好，世界"})


def get_parent_dir(user, parent_id: int | None):
    """获取父目录对象"""
    if parent_id is None:
        return None
    parent = UploadedFile.objects.filter(id=parent_id, created_by=user, is_dir=True).first()
    return parent


def get_scoped_parent_dir(user, parent_id: int | None, *, system_scope: bool = False):
    if not system_scope:
        return get_parent_dir(user, parent_id)
    if parent_id is None or not user.is_superuser:
        return None
    return UploadedFile.objects.filter(id=parent_id, is_dir=True, deleted_at__isnull=True).first()


def is_system_scope_request(request) -> bool:
    raw_scope = request.query_params.get("scope") if request.method == "GET" else request.data.get("scope")
    return str(raw_scope or "").strip().lower() == "system"


def parse_owner_user_id(raw_value) -> int | None:
    return parse_parent_id(raw_value)


def split_relative_upload_path(raw_relative_path: str, fallback_name: str) -> tuple[list[str], str]:
    """分割相对路径为目录列表和文件名"""
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
    """确保子文件夹存在"""
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
    """确保嵌套的父目录链路存在"""
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


def ensure_reference_can_be_saved_to_resource(user, source_reference: AssetReference) -> None:
    if source_reference.owner_user_id in {None, user.id}:
        return
    if source_reference.ref_domain != AssetReference.RefDomain.CHAT or source_reference.ref_type != AssetReference.RefType.CHAT_ATTACHMENT:
        raise PermissionDenied("当前无权保存该附件")

    conversation_id = parse_parent_id(source_reference.ref_object_id)
    if conversation_id is None:
        raise PermissionDenied("当前无权保存该附件")

    conversation = ChatConversation.objects.filter(
        id=conversation_id,
        deleted_at__isnull=True,
        status=ChatConversation.Status.ACTIVE,
    ).first()
    if conversation is None:
        raise PermissionDenied("当前无权保存该附件")

    get_conversation_access(user, conversation)


def file_item_payload(item: UploadedFile):
    asset, asset_reference = ensure_asset_compat_for_uploaded_file(item)
    expires_at = item.recycled_at + timedelta(days=RECYCLE_BIN_EXPIRE_DAYS) if item.recycled_at else None
    remaining_days = None
    if expires_at:
        remaining_days = max(0, (expires_at.date() - timezone.now().date()).days)

    return {
        "id": item.id,
        "display_name": item.display_name,
        "stored_name": item.stored_name,
        "resource_kind": "resource_center" if item.business != "chat" else "chat_upload",
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
        "recycled_at": item.recycled_at,
        "expires_at": expires_at,
        "remaining_days": remaining_days,
        "recycle_original_parent_id": item.recycle_original_parent_id,
        "owner_name": (item.created_by.display_name or item.created_by.username) if item.created_by is not None else "",
        "asset_reference_id": asset_reference.id,
        "asset_reference": serialize_asset_reference_payload(asset_reference),
        "asset": serialize_asset_payload(asset),
    }


def ensure_asset_refs_for_entries(entries: list[UploadedFile]) -> None:
    for entry in entries:
        if getattr(entry, "asset_reference_compat", None) is None:
            ensure_asset_compat_for_uploaded_file(entry)


def hard_delete_uploaded_entry(entry: UploadedFile) -> dict:
    subtree_ids = [entry.id]
    cursor = [entry.id]
    while cursor:
        child_ids = list(UploadedFile.all_objects.filter(parent_id__in=cursor).values_list("id", flat=True))
        if not child_ids:
            break
        subtree_ids.extend(child_ids)
        cursor = child_ids

    items = list(UploadedFile.all_objects.filter(id__in=subtree_ids).order_by("-is_dir", "-id"))
    asset_refs = list(AssetReference.all_objects.select_related("asset").filter(legacy_uploaded_file_id__in=subtree_ids))
    asset_ids = {item.asset_id for item in asset_refs if item.asset_id}
    file_paths = {str(item.relative_path or "") for item in items if not item.is_dir and item.relative_path}

    removed_db_files = sum(1 for item in items if not item.is_dir)
    removed_db_dirs = sum(1 for item in items if item.is_dir)

    AssetReference.all_objects.filter(id__in=[item.id for item in asset_refs]).delete()
    UploadedFile.all_objects.filter(id__in=subtree_ids).delete()

    removed_disk_files = 0
    for relative_path in file_paths:
        still_used_by_entry = UploadedFile.all_objects.filter(relative_path=relative_path).exists()
        still_used_by_asset = Asset.all_objects.filter(storage_key=relative_path).exclude(id__in=asset_ids).exists()
        if still_used_by_entry or still_used_by_asset:
            continue
        target_path = get_upload_root() / Path(relative_path)
        if target_path.exists() and target_path.is_file():
            target_path.unlink()
            removed_disk_files += 1

    for asset_id in asset_ids:
        if AssetReference.all_objects.filter(asset_id=asset_id).exists():
            continue
        Asset.all_objects.filter(id=asset_id).delete()

    return {
        "removed_db_files": removed_db_files,
        "removed_db_dirs": removed_db_dirs,
        "removed_disk_files": removed_disk_files,
    }


def file_reference_payload(reference: AssetReference):
    legacy_item = reference.legacy_uploaded_file
    is_dir = reference.ref_type == AssetReference.RefType.DIRECTORY
    expires_at = reference.recycled_at + timedelta(days=RECYCLE_BIN_EXPIRE_DAYS) if reference.recycled_at else None
    remaining_days = None
    if expires_at:
        remaining_days = max(0, (expires_at.date() - timezone.now().date()).days)

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
        "owner_user_id": (
            legacy_item.created_by_id
            if legacy_item is not None
            else reference.owner_user_id
        ),
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
        "recycled_at": reference.recycled_at,
        "expires_at": expires_at,
        "remaining_days": remaining_days,
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
        "owner_user_id": owner.id,
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
        "recycled_at": None,
        "expires_at": None,
        "remaining_days": None,
        "recycle_original_parent_id": None,
        "asset_reference_id": None,
        "asset_reference": None,
        "asset": None,
    }


class FileEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        get_user_upload_root(request.user)

        system_scope = is_system_scope_request(request)
        if system_scope and not request.user.is_superuser:
            return Response({"detail": "当前无权查看系统文件"}, status=status.HTTP_403_FORBIDDEN)

        parent_id = parse_parent_id(request.query_params.get("parent_id"))
        owner_user_id = parse_owner_user_id(request.query_params.get("owner_user_id")) if system_scope else None
        owner_user = None
        if system_scope and owner_user_id is not None:
            owner_user = User.objects.filter(id=owner_user_id, deleted_at__isnull=True).first()
            if owner_user is None:
                return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)

        if system_scope and parent_id is None and owner_user_id is None:
            owners = list(
                User.objects.filter(uploaded_files__deleted_at__isnull=True)
                .distinct()
                .order_by("username", "id")
            )
            return Response(
                {
                    "parent": None,
                    "breadcrumbs": [{"id": None, "name": "系统文件"}],
                    "items": [build_system_owner_folder_payload(owner) for owner in owners],
                    "owner_user": None,
                }
            )

        parent = get_scoped_parent_dir(owner_user or request.user, parent_id, system_scope=system_scope)
        if parent_id is not None and parent is None:
            return Response({"detail": "目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        if system_scope:
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

            return Response(
                {
                    "parent": None if parent is None else file_item_payload(parent),
                    "breadcrumbs": breadcrumbs,
                    "items": [file_item_payload(item) for item in entries],
                    "owner_user": None if owner_user is None else {"id": owner_user.id, "name": owner_user.display_name or owner_user.username},
                }
            )

        orphan_queryset = UploadedFile.objects.filter(parent=parent, deleted_at__isnull=True, asset_reference_compat__isnull=True)
        orphan_queryset = orphan_queryset.filter(created_by=request.user)
        orphan_entries = list(orphan_queryset)
        ensure_asset_refs_for_entries(orphan_entries)

        parent_reference = None if parent is None else ensure_asset_compat_for_uploaded_file(parent)[1]
        if parent is not None and is_recycle_bin_folder(parent):
            recycle_items = list(
                UploadedFile.objects.select_related("created_by")
                .filter(created_by=request.user, parent=parent, deleted_at__isnull=True)
                .order_by("recycled_at", "display_name", "id")
            )
            return Response(
                {
                    "parent": file_item_payload(parent),
                    "breadcrumbs": build_user_breadcrumbs_from_entry(parent),
                    "items": [file_item_payload(item) for item in recycle_items],
                    "owner_user": None,
                }
            )

        reference_items = list(
            AssetReference.objects.select_related("asset", "owner_user", "legacy_uploaded_file", "legacy_uploaded_file__created_by", "parent_reference", "parent_reference__legacy_uploaded_file")
            .filter(
                parent_reference=parent_reference,
                deleted_at__isnull=True,
                status=AssetReference.Status.ACTIVE,
            )
        )
        if system_scope:
            reference_items = []
        else:
            reference_items = [item for item in reference_items if item.owner_user_id == request.user.id and item.ref_domain == AssetReference.RefDomain.RESOURCE_CENTER]
        reference_items.sort(key=lambda item: (item.ref_type != AssetReference.RefType.DIRECTORY, item.display_name or "", item.id))
        items = [file_reference_payload(item) for item in reference_items]

        breadcrumbs = [{"id": None, "name": "我的文件"}]
        if parent_reference:
            chain: list[AssetReference] = []
            cursor = parent_reference
            while cursor:
                chain.append(cursor)
                cursor = cursor.parent_reference
            for node in reversed(chain):
                breadcrumbs.append({"id": node.legacy_uploaded_file_id or node.id, "name": node.display_name, "owner_user_id": owner_user.id if owner_user is not None else None})

        return Response(
            {
                "parent": None if not parent_reference else file_reference_payload(parent_reference),
                "breadcrumbs": breadcrumbs,
                "items": items,
                "owner_user": None,
            }
        )


class SearchFileEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        keyword = str(request.query_params.get("keyword", "")).strip()
        if not keyword:
            return Response({"items": []})

        system_scope = is_system_scope_request(request)
        if system_scope and not request.user.is_superuser:
            return Response({"detail": "当前无权搜索系统文件"}, status=status.HTTP_403_FORBIDDEN)

        owner_user_id = parse_owner_user_id(request.query_params.get("owner_user_id")) if system_scope else None

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        orphan_queryset = UploadedFile.objects.filter(
            is_dir=False,
            display_name__icontains=keyword,
            deleted_at__isnull=True,
            asset_reference_compat__isnull=True,
        )
        if not system_scope:
            orphan_queryset = orphan_queryset.filter(created_by=request.user)
            orphan_entries = list(orphan_queryset[:limit])
            ensure_asset_refs_for_entries(orphan_entries)

        if system_scope:
            entry_queryset = UploadedFile.objects.select_related("created_by", "parent").filter(display_name__icontains=keyword, deleted_at__isnull=True)
            if owner_user_id is not None:
                entry_queryset = entry_queryset.filter(created_by_id=owner_user_id)
            matched_entries = list(entry_queryset.order_by("display_name", "id")[:limit])

            def build_entry_path(entry: UploadedFile) -> str:
                owner_name = (entry.created_by.display_name or entry.created_by.username) if entry.created_by is not None else "未知用户"
                chain: list[str] = []
                cursor = entry.parent
                while cursor is not None:
                    chain.append(cursor.display_name)
                    cursor = cursor.parent
                chain.reverse()
                directory_path = "/".join(chain)
                full_path = f"{owner_name}/{directory_path}/{entry.display_name}" if directory_path else f"{owner_name}/{entry.display_name}"
                payload = file_item_payload(entry)
                payload["directory_path"] = directory_path
                payload["full_path"] = full_path
                return payload

            return Response({"items": [build_entry_path(item) for item in matched_entries]})

        matched_files = list(
            AssetReference.objects.select_related("asset", "owner_user", "legacy_uploaded_file", "legacy_uploaded_file__created_by", "parent_reference", "parent_reference__legacy_uploaded_file")
            .filter(
                ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
                ref_type__in=[AssetReference.RefType.FILE, AssetReference.RefType.AVATAR],
                display_name__icontains=keyword,
                deleted_at__isnull=True,
                status=AssetReference.Status.ACTIVE,
                owner_user_id=request.user.id,
            )
            .order_by("display_name", "id")[:limit]
        )

        all_dirs_queryset = AssetReference.objects.filter(
            ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
            ref_type=AssetReference.RefType.DIRECTORY,
            deleted_at__isnull=True,
            status=AssetReference.Status.ACTIVE,
        )
        if not system_scope:
            all_dirs_queryset = all_dirs_queryset.filter(owner_user=request.user)
        elif owner_user_id is not None:
            all_dirs_queryset = all_dirs_queryset.filter(owner_user_id=owner_user_id)
        all_dirs = all_dirs_queryset.values("id", "parent_reference_id", "display_name")
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

        items = []
        for file_item in matched_files:
            payload = file_reference_payload(file_item)
            directory_path = build_dir_path(file_item.parent_reference_id)
            full_path = f"{directory_path}/{file_item.display_name}" if directory_path else file_item.display_name
            payload["directory_path"] = directory_path
            payload["full_path"] = full_path
            items.append(payload)

        return Response({"items": items})


class CreateFolderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        get_user_upload_root(request.user)

        parent_id = parse_parent_id(request.data.get("parent_id"))
        parent = get_parent_dir(request.user, parent_id)
        if parent_id is not None and parent is None:
            return Response({"detail": "父目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        folder_name = str(request.data.get("name", "")).strip()
        if not folder_name:
            return Response({"detail": "文件夹名称不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        if "/" in folder_name or "\\" in folder_name:
            return Response({"detail": "文件夹名称不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if is_reserved_system_folder_name(folder_name):
            return Response({"detail": '“回收站”是系统保留目录名称，请使用其他名称'}, status=status.HTTP_400_BAD_REQUEST)

        conflict = UploadedFile.objects.filter(created_by=request.user, parent=parent, display_name=folder_name).exists()
        if conflict:
            return Response({"detail": "同名文件或文件夹已存在"}, status=status.HTTP_400_BAD_REQUEST)

        folder = ensure_child_folder(request.user, parent, folder_name)
        return Response(file_item_payload(folder), status=status.HTTP_201_CREATED)


class SaveChatAttachmentToResourceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        source_asset_reference_id = parse_parent_id(request.data.get("source_asset_reference_id"))
        if source_asset_reference_id is None:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)

        parent_id = parse_parent_id(request.data.get("parent_id"))
        parent = get_parent_dir(request.user, parent_id)
        if parent_id is not None and parent is None:
            return Response({"detail": "目录不存在"}, status=status.HTTP_404_NOT_FOUND)
        if parent is not None and is_recycle_bin_folder(parent):
            return Response({"detail": "资源中心目录不能选择回收站"}, status=status.HTTP_400_BAD_REQUEST)

        source_reference = AssetReference.objects.select_related("asset").filter(id=source_asset_reference_id, deleted_at__isnull=True).first()
        if source_reference is None or source_reference.asset is None:
            return Response({"detail": "聊天附件不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            ensure_reference_can_be_saved_to_resource(request.user, source_reference)
        except PermissionDenied:
            return Response({"detail": "当前无权保存该附件"}, status=status.HTTP_403_FORBIDDEN)

        display_name = str(request.data.get("display_name", "")).strip() or source_reference.display_name or source_reference.asset.original_name or "附件"
        if "/" in display_name or "\\" in display_name:
            return Response({"detail": "文件名不合法"}, status=status.HTTP_400_BAD_REQUEST)

        source_relative_path = source_reference.relative_path_cache or source_reference.asset.storage_key or ""
        existing, _ = resolve_existing_upload_file(
            request.user,
            source_reference.asset.file_md5 or "",
            parent,
            display_name,
            relative_path=source_relative_path,
            business="",
        )
        if existing is None:
            relative_path = source_relative_path
            stored_name = Path(relative_path).name or build_stored_name(display_name)
            existing = UploadedFile.objects.create(
                created_by=request.user,
                parent=parent,
                is_dir=False,
                business="",
                display_name=display_name,
                stored_name=stored_name,
                file_md5=source_reference.asset.file_md5 or "",
                file_size=source_reference.asset.file_size,
                relative_path=relative_path,
            )

        return Response({"detail": "已保存到资源中心", "file": file_item_payload(existing)}, status=status.HTTP_201_CREATED)


class DeleteFileEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        system_scope = is_system_scope_request(request)
        if system_scope and not request.user.is_superuser:
            return Response({"detail": "当前无权删除系统文件"}, status=status.HTTP_403_FORBIDDEN)

        entry_id = parse_parent_id(request.data.get("id"))
        if entry_id is None:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)
        entry_queryset = UploadedFile.objects.filter(id=entry_id)
        if not system_scope:
            entry_queryset = entry_queryset.filter(created_by=request.user)
        entry = entry_queryset.first()
        if not entry:
            return Response({"detail": "文件或目录不存在"}, status=status.HTTP_404_NOT_FOUND)
        if is_recycle_bin_folder(entry):
            return Response({"detail": "回收站目录不可删除"}, status=status.HTTP_400_BAD_REQUEST)

        if system_scope:
            result = hard_delete_uploaded_entry(entry)
            return Response({"detail": "已彻底删除", **result})

        if entry.recycled_at is not None:
            return Response({"detail": "该文件已在回收站，请前往回收站还原"}, status=status.HTTP_400_BAD_REQUEST)

        moved_count = move_entry_to_recycle_bin(entry)
        return Response({"detail": "已移入回收站", "moved_count": moved_count})


class RenameFileEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        entry_id = parse_parent_id(request.data.get("id"))
        new_name = str(request.data.get("name", "")).strip()
        if entry_id is None or not new_name:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if "/" in new_name or "\\" in new_name:
            return Response({"detail": "名称不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if is_reserved_system_folder_name(new_name):
            return Response({"detail": '“回收站”是系统保留目录名称，请使用其他名称'}, status=status.HTTP_400_BAD_REQUEST)

        entry = UploadedFile.objects.filter(id=entry_id, created_by=request.user).first()
        if not entry:
            return Response({"detail": "文件或目录不存在"}, status=status.HTTP_404_NOT_FOUND)
        if is_recycle_bin_folder(entry):
            return Response({"detail": "回收站目录不可重命名"}, status=status.HTTP_400_BAD_REQUEST)

        conflict = UploadedFile.objects.filter(
            created_by=request.user,
            parent=entry.parent,
            display_name=new_name,
        ).exclude(id=entry.id)
        if conflict.exists():
            return Response({"detail": "同名文件或文件夹已存在"}, status=status.HTTP_400_BAD_REQUEST)

        entry.display_name = new_name
        entry.save(update_fields=["display_name", "updated_at"])
        return Response(file_item_payload(entry))


class RecycleBinEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"items": list_recycle_bin_entries(request.user)})


class RestoreRecycleBinEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        entry_id = parse_parent_id(request.data.get("id"))
        if entry_id is None:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)

        entry = UploadedFile.objects.filter(id=entry_id, created_by=request.user).first()
        if not entry:
            return Response({"detail": "文件或目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        try:
            restored = restore_entry_from_recycle_bin(entry)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"detail": "已从回收站还原", "item": file_item_payload(restored)})


class ClearRecycleBinAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response({"detail": "只有系统资源支持彻底删除"}, status=status.HTTP_400_BAD_REQUEST)


class UploadSmallFileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj = request.FILES.get("file")
        category = parse_category(request.data.get("category", ""))
        parent_id = parse_parent_id(request.data.get("parent_id"))
        parent = get_parent_dir(request.user, parent_id)
        relative_path = str(request.data.get("relative_path", ""))

        if not file_obj:
            return Response({"detail": "缺少文件"}, status=status.HTTP_400_BAD_REQUEST)
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if parent_id is not None and parent is None:
            return Response({"detail": "目标目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        nested_folders, display_name = split_relative_upload_path(relative_path, file_obj.name)
        target_parent = ensure_nested_parent(request.user, parent, nested_folders)

        # 头像文件单独存储，不进入用户文件管理树
        if category == "profile":
            if parent_id is not None or normalize_relative_path(relative_path):
                return Response({"detail": "头像上传不支持目录参数"}, status=status.HTTP_400_BAD_REQUEST)

            avatar_file_error = validate_avatar_upload_file(file_obj)
            if avatar_file_error:
                return Response({"detail": avatar_file_error}, status=status.HTTP_400_BAD_REQUEST)

            display_name = file_obj.name
            target_dir = get_avatar_upload_root(request.user)
            target_dir.mkdir(parents=True, exist_ok=True)

            stored_name = build_stored_name(display_name)
            target_path = target_dir / stored_name
            with target_path.open("wb") as f:
                for chunk in file_obj.chunks():
                    f.write(chunk)

            avatar_relative_path = relative_to_uploads(target_path)
            avatar_url = media_url(avatar_relative_path)
            avatar_asset, avatar_reference = create_user_profile_asset_reference(
                user=request.user,
                display_name=display_name,
                relative_path=avatar_relative_path,
                file_size=int(file_obj.size),
            )
            return Response(
                {
                    "mode": "direct",
                    "file": {
                        "id": 0,
                        "display_name": display_name,
                        "stored_name": stored_name,
                        "is_dir": False,
                        "parent_id": None,
                        "file_size": int(file_obj.size),
                        "file_md5": "",
                        "relative_path": avatar_relative_path,
                        "url": avatar_url,
                        "created_at": None,
                        "updated_at": None,
                        "is_system": False,
                        "is_recycle_bin": False,
                        "recycled_at": None,
                        "expires_at": None,
                        "remaining_days": None,
                        "recycle_original_parent_id": None,
                        "asset_reference_id": avatar_reference.id,
                        "asset_reference": serialize_asset_reference_payload(avatar_reference),
                        "asset": serialize_asset_payload(avatar_asset),
                    },
                }
            )

        file_md5 = calc_uploaded_file_md5(file_obj)
        existing, restored_from_recycle = resolve_existing_upload_file(request.user, file_md5, target_parent, display_name)
        if existing:
            return Response(
                {
                    "mode": "instant",
                    "restored_from_recycle": restored_from_recycle,
                    "file": file_item_payload(existing),
                }
            )

        target_dir = get_user_upload_root(request.user) if not target_parent else (get_upload_root() / Path(target_parent.relative_path))
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_name = build_stored_name(display_name)
        target_path = target_dir / stored_name
        with target_path.open("wb") as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

        relative_path = relative_to_uploads(target_path)
        file_record = UploadedFile.objects.create(
            file_md5=file_md5,
            file_size=int(file_obj.size),
            relative_path=relative_path,
            created_by=request.user,
            parent=target_parent,
            is_dir=False,
            stored_name=stored_name,
            display_name=display_name,
            business=category,
        )
        asset, _ = ensure_asset_compat_for_uploaded_file(file_record)
        ensure_video_asset_pipeline(asset)

        return Response(
            {
                "mode": "direct",
                "file": file_item_payload(file_record),
            }
        )


class UploadPrecheckAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_md5 = str(request.data.get("file_md5", "")).strip().lower()
        file_name = str(request.data.get("file_name", "")).strip()
        category = parse_category(request.data.get("category", ""))
        parent_id = parse_parent_id(request.data.get("parent_id"))
        parent = get_parent_dir(request.user, parent_id)
        relative_path = str(request.data.get("relative_path", ""))
        try:
            file_size = int(request.data.get("file_size", 0))
        except (TypeError, ValueError):
            file_size = 0

        if len(file_md5) != 32:
            return Response({"detail": "file_md5不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if not file_name:
            return Response({"detail": "缺少file_name"}, status=status.HTTP_400_BAD_REQUEST)
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if file_size <= 0:
            return Response({"detail": "file_size不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if category == "profile":
            return Response({"detail": "头像上传请走小文件直传接口"}, status=status.HTTP_400_BAD_REQUEST)
        if parent_id is not None and parent is None:
            return Response({"detail": "目标目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        nested_folders, display_name = split_relative_upload_path(relative_path, file_name)
        target_parent = ensure_nested_parent(request.user, parent, nested_folders)

        existing, restored_from_recycle = resolve_existing_upload_file(request.user, file_md5, target_parent, display_name)
        if existing:
            return Response(
                {
                    "exists": True,
                    "message": "文件已在回收站中恢复" if restored_from_recycle else "文件已存在，秒传成功",
                    "restored_from_recycle": restored_from_recycle,
                    "file": file_item_payload(existing),
                }
            )

        return Response(
            {
                "exists": False,
                "message": "文件不存在，开始分片上传",
            }
        )


class UploadedChunksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        file_md5 = str(request.query_params.get("file_md5", "")).strip().lower()
        if len(file_md5) != 32:
            return Response({"detail": "file_md5不合法"}, status=status.HTTP_400_BAD_REQUEST)

        temp_dir = get_temp_root() / f"{request.user.id}_{file_md5}"
        if not temp_dir.exists():
            return Response({"uploaded_chunks": []})

        uploaded_chunks: list[int] = []
        for path in temp_dir.iterdir():
            if path.is_file() and path.name.isdigit():
                uploaded_chunks.append(int(path.name))
        uploaded_chunks.sort()
        return Response({"uploaded_chunks": uploaded_chunks})


class UploadChunkAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj = request.FILES.get("chunk")
        file_md5 = str(request.data.get("file_md5", "")).strip().lower()
        try:
            chunk_index = int(request.data.get("chunk_index", 0))
        except (TypeError, ValueError):
            chunk_index = 0
        chunk_md5 = str(request.data.get("chunk_md5", "")).strip().lower()

        if not file_obj:
            return Response({"detail": "缺少分片文件"}, status=status.HTTP_400_BAD_REQUEST)
        if len(file_md5) != 32 or chunk_index <= 0:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)

        temp_dir = get_temp_root() / f"{request.user.id}_{file_md5}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = temp_dir / str(chunk_index)

        with chunk_path.open("wb") as f:
            for part in file_obj.chunks():
                f.write(part)

        if len(chunk_md5) == 32:
            if not verify_chunk_md5(chunk_path, chunk_md5):
                chunk_path.unlink(missing_ok=True)
                return Response({"detail": "分片MD5校验失败"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"chunk_index": chunk_index, "uploaded": True})


class UploadMergeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_md5 = str(request.data.get("file_md5", "")).strip().lower()
        total_md5 = str(request.data.get("total_md5", "")).strip().lower()
        file_name = str(request.data.get("file_name", "")).strip()
        category = parse_category(request.data.get("category", ""))
        parent_id = parse_parent_id(request.data.get("parent_id"))
        parent = get_parent_dir(request.user, parent_id)
        relative_path = str(request.data.get("relative_path", ""))
        try:
            total_chunks = int(request.data.get("total_chunks", 0))
            file_size = int(request.data.get("file_size", 0))
        except (TypeError, ValueError):
            total_chunks = 0
            file_size = 0

        if len(file_md5) != 32 or len(total_md5) != 32 or not file_name or total_chunks <= 0:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if category == "profile":
            return Response({"detail": "头像上传请走小文件直传接口"}, status=status.HTTP_400_BAD_REQUEST)
        if file_size <= 0:
            return Response({"detail": "file_size不合法"}, status=status.HTTP_400_BAD_REQUEST)
        if parent_id is not None and parent is None:
            return Response({"detail": "目标目录不存在"}, status=status.HTTP_404_NOT_FOUND)

        nested_folders, display_name = split_relative_upload_path(relative_path, file_name)
        target_parent = ensure_nested_parent(request.user, parent, nested_folders)

        temp_dir = get_temp_root() / f"{request.user.id}_{file_md5}"
        missing_chunks: list[int] = []
        for idx in range(1, total_chunks + 1):
            if not (temp_dir / str(idx)).exists():
                missing_chunks.append(idx)

        if missing_chunks:
            return Response(
                {
                    "detail": "分片未上传完整",
                    "missing_chunks": missing_chunks,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            inspector = celery_app.control.inspect(timeout=1)
            online_workers = inspector.ping() or {}
        except Exception:
            online_workers = {}

        if not online_workers:
            return Response(
                {
                    "detail": "后台合并服务不可用（未检测到 Celery Worker）",
                    "hint": "请先启动 Celery Worker：celery -A bbot_server worker -l info",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        task = merge_large_file_task.delay(
            file_md5=file_md5,
            total_chunks=total_chunks,
            file_name=file_name,
            display_name=display_name,
            total_md5=total_md5,
            file_size=file_size,
            user_id=request.user.id,
            parent_id=target_parent.id if target_parent else None,
            business=category,
        )
        return Response({"task_id": task.id, "message": "已提交后台合并任务"})