from datetime import datetime
from pathlib import Path

from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from hyself.auth.permissions import ensure_reference_can_be_saved_to_resource, resolve_upload_permission_code
from hyself.asset_compat import create_user_profile_asset_reference, ensure_asset_compat_for_uploaded_file
from hyself.application.services.resource_payloads import (
    build_avatar_upload_payload,
    build_reference_search_payload,
    build_system_search_entry_payload,
    file_item_payload,
    file_reference_payload,
)
from hyself.application.services.resource_center import (
    build_system_owner_folder_payload,
    build_user_breadcrumbs_from_entry,
    build_video_artifacts_root_payload,
    ensure_asset_refs_for_entries,
    ensure_child_folder,
    ensure_nested_parent,
    entry_is_within_recycle_bin_tree,
    get_parent_dir,
    get_scoped_parent_dir,
    is_reserved_system_folder_name,
    is_system_scope_request,
    resolve_existing_upload_file,
    resolve_video_artifact_virtual_items,
    split_relative_upload_path,
)
from hyself.models import Asset, AssetReference, UploadedFile
from hyself.video_processing import ensure_video_asset_pipeline
from hyself.recycle_bin import (
    clear_recycle_bin,
    ensure_user_recycle_bin,
    is_recycle_bin_folder,
    list_recycle_bin_entries,
    move_entry_to_recycle_bin,
    restore_entry_from_recycle_bin,
)
from hyself_server.celery import app as celery_app
from hyself.tasks import merge_large_file_task
from hyself.utils.upload import (
    build_stored_name,
    calc_uploaded_file_md5,
    get_avatar_upload_root,
    get_temp_root,
    get_upload_root,
    get_user_relative_root,
    get_user_upload_root,
    media_url,
    normalize_relative_path,
    relative_to_uploads,
    verify_chunk_md5,
)
from hyself.validators import parse_category, parse_owner_user_id, parse_parent_id, parse_virtual_path, validate_avatar_upload_file


User = get_user_model()


def index(request):
    return JsonResponse({"data": "你好，世界"})


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




class FileEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ensure_request_permission(request, "file.view_resource")
        get_user_upload_root(request.user)

        system_scope = is_system_scope_request(request)
        if system_scope and not request.user.is_superuser:
            return Response({"detail": "当前无权查看系统文件"}, status=status.HTTP_403_FORBIDDEN)

        parent_id = parse_parent_id(request.query_params.get("parent_id"))
        owner_user_id = parse_owner_user_id(request.query_params.get("owner_user_id")) if system_scope else None
        virtual_path = parse_virtual_path(request.query_params.get("virtual_path")) if system_scope else None
        owner_user = None
        if system_scope and owner_user_id is not None:
            owner_user = User.objects.filter(id=owner_user_id, deleted_at__isnull=True).first()
            if owner_user is None:
                return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)

        if system_scope and virtual_path:
            try:
                parent_payload, items, breadcrumbs = resolve_video_artifact_virtual_items(virtual_path)
            except FileNotFoundError:
                return Response({"detail": "目录不存在"}, status=status.HTTP_404_NOT_FOUND)
            return Response(
                {
                    "parent": parent_payload,
                    "breadcrumbs": breadcrumbs,
                    "items": items,
                    "owner_user": None,
                }
            )

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
                    "items": [build_video_artifacts_root_payload(), *[build_system_owner_folder_payload(owner) for owner in owners]],
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
                    "parent": None if parent is None else file_item_payload(parent, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
                    "breadcrumbs": breadcrumbs,
                    "items": [file_item_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in entries],
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
                    "parent": file_item_payload(parent, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
                    "breadcrumbs": build_user_breadcrumbs_from_entry(parent),
                    "items": [file_item_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in recycle_items],
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
        items = [file_reference_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in reference_items]

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
                "parent": None if not parent_reference else file_reference_payload(parent_reference, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
                "breadcrumbs": breadcrumbs,
                "items": items,
                "owner_user": None,
            }
        )


class SearchFileEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ensure_request_permission(request, "file.view_resource")
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

            return Response({"items": [build_system_search_entry_payload(item, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree) for item in matched_entries]})

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
            directory_path = build_dir_path(file_item.parent_reference_id)
            items.append(build_reference_search_payload(file_item, directory_path=directory_path, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree))

        return Response({"items": items})


class CreateFolderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.create_folder")
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
        return Response(file_item_payload(folder, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree), status=status.HTTP_201_CREATED)


class SaveChatAttachmentToResourceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.save_chat_attachment")
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

        return Response({"detail": "已保存到资源中心", "file": file_item_payload(existing, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)}, status=status.HTTP_201_CREATED)


class DeleteFileEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        system_scope = is_system_scope_request(request)
        ensure_request_permission(request, "file.manage_system_resource" if system_scope else "file.delete_resource")
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
        if system_scope and entry.created_by_id != request.user.id and entry_is_within_recycle_bin_tree(entry):
            return Response({"detail": "系统资源中不能删除其他用户回收站内的文件或目录"}, status=status.HTTP_403_FORBIDDEN)

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
        ensure_request_permission(request, "file.rename_resource")
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
        return Response(file_item_payload(entry, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree))


class RecycleBinEntriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ensure_request_permission(request, "file.view_resource")
        return Response({"items": list_recycle_bin_entries(request.user)})


class RestoreRecycleBinEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.restore_resource")
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

        return Response({"detail": "已从回收站还原", "item": file_item_payload(restored, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)})


class ClearRecycleBinAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.manage_system_resource")
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
        ensure_request_permission(request, resolve_upload_permission_code(category))
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
            avatar_asset, avatar_reference = create_user_profile_asset_reference(
                user=request.user,
                display_name=display_name,
                relative_path=avatar_relative_path,
                file_size=int(file_obj.size),
            )
            return Response(build_avatar_upload_payload(display_name=display_name, stored_name=stored_name, relative_path=avatar_relative_path, file_size=int(file_obj.size), asset=avatar_asset, asset_reference=avatar_reference))

        file_md5 = calc_uploaded_file_md5(file_obj)
        existing, restored_from_recycle = resolve_existing_upload_file(request.user, file_md5, target_parent, display_name)
        if existing:
            return Response(
                {
                    "mode": "instant",
                    "restored_from_recycle": restored_from_recycle,
                    "file": file_item_payload(existing, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
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
                "file": file_item_payload(file_record, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
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
        ensure_request_permission(request, resolve_upload_permission_code(category))
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
                    "file": file_item_payload(existing, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
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
        category = parse_category(request.query_params.get("category", ""))
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        ensure_request_permission(request, resolve_upload_permission_code(category))
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
        category = parse_category(request.data.get("category", ""))
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        ensure_request_permission(request, resolve_upload_permission_code(category))
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
        ensure_request_permission(request, resolve_upload_permission_code(category))
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
                    "hint": "请先启动 Celery Worker：celery -A hyself_server worker -l info",
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