from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.http import JsonResponse
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from hyself.auth.permissions import resolve_upload_permission_code
from hyself.application.commands.resource_center import (
    delete_resource_entry,
    rename_resource_entry,
    restore_resource_entry,
    save_chat_attachment_to_resource,
)
from hyself.application.commands.resource_uploads import (
    UploadMergeServiceUnavailableError,
    create_folder_entry,
    precheck_file_upload,
    process_small_file_upload,
    store_upload_chunk,
    submit_large_file_merge,
)
from hyself.application.payloads.resource_center import (
    build_uploaded_file_payload,
)
from hyself.application.queries.resource_center import (
    build_scoped_file_entries_payload,
    build_scoped_search_payload,
)
from hyself.application.services.resource_center import entry_is_within_recycle_bin_tree, is_system_scope_request
from hyself.recycle_bin import (
    list_recycle_bin_entries,
)
from hyself.utils.upload import (
    get_temp_root,
    get_user_upload_root,
)
from hyself.validators import parse_category, parse_owner_user_id, parse_parent_id, parse_virtual_path
def index(request):
    return JsonResponse({"data": "你好，世界"})


def _validation_detail(exc: ValidationError):
    return exc.detail.get("detail") if isinstance(exc.detail, dict) else exc.detail


def _response_from_validation_error(exc: ValidationError, *, not_found_details: tuple[str, ...] = ()):
    detail = _validation_detail(exc)
    if detail in not_found_details:
        return Response({"detail": detail}, status=status.HTTP_404_NOT_FOUND)
    body = {"detail": detail}
    if isinstance(exc.detail, dict):
        for key, value in exc.detail.items():
            if key != "detail":
                body[key] = value
    return Response(body, status=status.HTTP_400_BAD_REQUEST)




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
        try:
            payload = build_scoped_file_entries_payload(
                user=request.user,
                system_scope=system_scope,
                parent_id=parent_id,
                owner_user_id=owner_user_id,
                virtual_path=virtual_path,
            )
        except ValidationError as exc:
            return _response_from_validation_error(
                exc,
                not_found_details=("目标用户不存在", "目录不存在"),
            )

        return Response(payload)


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

        return Response(
            build_scoped_search_payload(
                user=request.user,
                system_scope=system_scope,
                keyword=keyword,
                limit=limit,
                owner_user_id=owner_user_id,
            )
        )


class CreateFolderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.create_folder")
        get_user_upload_root(request.user)
        try:
            folder = create_folder_entry(
                user=request.user,
                parent_id=parse_parent_id(request.data.get("parent_id")),
                folder_name=str(request.data.get("name", "")),
            )
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("父目录不存在",))

        return Response(build_uploaded_file_payload(folder, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree), status=status.HTTP_201_CREATED)


class SaveChatAttachmentToResourceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.save_chat_attachment")
        source_asset_reference_id = parse_parent_id(request.data.get("source_asset_reference_id"))
        if source_asset_reference_id is None:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            entry = save_chat_attachment_to_resource(
                user=request.user,
                source_asset_reference_id=source_asset_reference_id,
                parent_id=parse_parent_id(request.data.get("parent_id")),
                display_name=str(request.data.get("display_name", "")),
            )
        except PermissionDenied:
            return Response({"detail": "当前无权保存该附件"}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("聊天附件不存在", "目录不存在"))

        return Response({"detail": "已保存到资源中心", "file": build_uploaded_file_payload(entry, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)}, status=status.HTTP_201_CREATED)


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
        if system_scope:
            try:
                result = delete_resource_entry(acting_user=request.user, entry_id=entry_id, system_scope=True)
            except PermissionDenied as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
            except ValidationError as exc:
                return _response_from_validation_error(exc, not_found_details=("文件或目录不存在",))
            return Response(result)

        try:
            result = delete_resource_entry(acting_user=request.user, entry_id=entry_id, system_scope=False)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("文件或目录不存在",))
        return Response(result)


class RenameFileEntryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "file.rename_resource")
        entry_id = parse_parent_id(request.data.get("id"))
        new_name = str(request.data.get("name", "")).strip()
        if entry_id is None or not new_name:
            return Response({"detail": "参数不合法"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            entry = rename_resource_entry(user=request.user, entry_id=entry_id, new_name=new_name)
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("文件或目录不存在",))
        return Response(build_uploaded_file_payload(entry, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree))


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
        try:
            restored = restore_resource_entry(user=request.user, entry_id=entry_id)
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("文件或目录不存在",))

        return Response({"detail": "已从回收站还原", "item": build_uploaded_file_payload(restored, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree)})


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
        relative_path = str(request.data.get("relative_path", ""))

        if not file_obj:
            return Response({"detail": "缺少文件"}, status=status.HTTP_400_BAD_REQUEST)
        if category is None:
            return Response({"detail": "category不合法"}, status=status.HTTP_400_BAD_REQUEST)
        ensure_request_permission(request, resolve_upload_permission_code(category))
        try:
            result = process_small_file_upload(
                user=request.user,
                file_obj=file_obj,
                category=category,
                parent_id=parent_id,
                relative_path=relative_path,
            )
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("目标目录不存在",))

        return Response(result)


class UploadPrecheckAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_md5 = str(request.data.get("file_md5", "")).strip().lower()
        file_name = str(request.data.get("file_name", "")).strip()
        category = parse_category(request.data.get("category", ""))
        parent_id = parse_parent_id(request.data.get("parent_id"))
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
        try:
            result = precheck_file_upload(
                user=request.user,
                file_md5=file_md5,
                file_name=file_name,
                file_size=file_size,
                parent_id=parent_id,
                relative_path=relative_path,
            )
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("目标目录不存在",))

        return Response(result)


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
        try:
            result = store_upload_chunk(
                user=request.user,
                file_md5=file_md5,
                chunk_index=chunk_index,
                chunk_md5=chunk_md5,
                file_obj=file_obj,
            )
        except ValidationError as exc:
            return _response_from_validation_error(exc)

        return Response(result)


class UploadMergeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_md5 = str(request.data.get("file_md5", "")).strip().lower()
        total_md5 = str(request.data.get("total_md5", "")).strip().lower()
        file_name = str(request.data.get("file_name", "")).strip()
        category = parse_category(request.data.get("category", ""))
        parent_id = parse_parent_id(request.data.get("parent_id"))
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
        try:
            result = submit_large_file_merge(
                user=request.user,
                file_md5=file_md5,
                total_md5=total_md5,
                file_name=file_name,
                total_chunks=total_chunks,
                file_size=file_size,
                parent_id=parent_id,
                relative_path=relative_path,
                category=category,
            )
        except UploadMergeServiceUnavailableError as exc:
            return Response({"detail": exc.detail, "hint": exc.hint}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValidationError as exc:
            return _response_from_validation_error(exc, not_found_details=("目标目录不存在",))

        return Response(result)