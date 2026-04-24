from __future__ import annotations

from pathlib import Path

from django.conf import settings
from rest_framework.exceptions import ValidationError

from hyself.asset_compat import create_user_profile_asset_reference, ensure_asset_compat_for_uploaded_file
from hyself.audio_processing import ensure_audio_asset_pipeline
from hyself.application.payloads.resource_center import (
    build_avatar_upload_response_payload,
    build_uploaded_file_payload,
)
from hyself.infrastructure.event_bus import notify_resource_entry_created, notify_resource_entry_moved
from hyself.application.services.resource_center import (
    ensure_child_folder,
    ensure_nested_parent,
    entry_is_within_recycle_bin_tree,
    get_parent_dir,
    is_reserved_system_folder_name,
    resolve_existing_upload_file,
    split_relative_upload_path,
)
from hyself.models import UploadedFile
from hyself.video_processing import ensure_video_asset_pipeline
from hyself_server.celery import app as celery_app
from hyself.tasks import merge_large_file_task
from hyself.utils.upload import (
    build_stored_name,
    calc_uploaded_file_md5,
    get_avatar_upload_root,
    get_temp_root,
    get_upload_root,
    get_user_upload_root,
    normalize_relative_path,
    relative_to_uploads,
    verify_chunk_md5,
)
from hyself.validators import validate_avatar_upload_file


UPLOAD_MAX_FILE_SIZE = int(getattr(settings, "UPLOAD_MAX_FILE_SIZE", 1024 * 1024 * 1024))


def _validate_upload_file_size(file_size: int) -> None:
    if file_size <= 0:
        raise ValidationError({"detail": "file_size不合法"})
    if file_size > UPLOAD_MAX_FILE_SIZE:
        raise ValidationError({"detail": f"文件不能超过 {UPLOAD_MAX_FILE_SIZE // 1024 // 1024}MB"})


class UploadMergeServiceUnavailableError(Exception):
    def __init__(self, detail: str, hint: str):
        super().__init__(detail)
        self.detail = detail
        self.hint = hint


def create_folder_entry(*, user, parent_id: int | None, folder_name: str) -> UploadedFile:
    parent = get_parent_dir(user, parent_id)
    if parent_id is not None and parent is None:
        raise ValidationError({"detail": "父目录不存在"})

    normalized_name = str(folder_name).strip()
    if not normalized_name:
        raise ValidationError({"detail": "文件夹名称不能为空"})
    if "/" in normalized_name or "\\" in normalized_name:
        raise ValidationError({"detail": "文件夹名称不合法"})
    if is_reserved_system_folder_name(normalized_name):
        raise ValidationError({"detail": '“回收站”是系统保留目录名称，请使用其他名称'})

    conflict = UploadedFile.objects.filter(created_by=user, parent=parent, display_name=normalized_name).exists()
    if conflict:
        raise ValidationError({"detail": "同名文件或文件夹已存在"})

    folder = ensure_child_folder(user, parent, normalized_name)
    notify_resource_entry_created(folder)
    return folder


def process_small_file_upload(*, user, file_obj, category: str, parent_id: int | None, relative_path: str) -> dict:
    _validate_upload_file_size(int(getattr(file_obj, "size", 0) or 0))

    parent = get_parent_dir(user, parent_id)
    if parent_id is not None and parent is None:
        raise ValidationError({"detail": "目标目录不存在"})

    nested_folders, display_name = split_relative_upload_path(relative_path, file_obj.name)
    target_parent = ensure_nested_parent(user, parent, nested_folders)

    if category == "profile":
        if parent_id is not None or normalize_relative_path(relative_path):
            raise ValidationError({"detail": "头像上传不支持目录参数"})

        avatar_file_error = validate_avatar_upload_file(file_obj)
        if avatar_file_error:
            raise ValidationError({"detail": avatar_file_error})

        display_name = file_obj.name
        target_dir = get_avatar_upload_root(user)
        target_dir.mkdir(parents=True, exist_ok=True)

        stored_name = build_stored_name(display_name)
        target_path = target_dir / stored_name
        with target_path.open("wb") as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

        avatar_relative_path = relative_to_uploads(target_path)
        avatar_asset, avatar_reference = create_user_profile_asset_reference(
            user=user,
            display_name=display_name,
            relative_path=avatar_relative_path,
            file_size=int(file_obj.size),
        )
        return build_avatar_upload_response_payload(
            display_name=display_name,
            stored_name=stored_name,
            relative_path=avatar_relative_path,
            file_size=int(file_obj.size),
            asset=avatar_asset,
            asset_reference=avatar_reference,
        )

    file_md5 = calc_uploaded_file_md5(file_obj)
    existing, restored_from_recycle, restored_from_parent_id = resolve_existing_upload_file(user, file_md5, target_parent, display_name)
    if existing:
        if restored_from_recycle:
            notify_resource_entry_moved(
                owner_user_id=existing.created_by_id,
                entry_id=existing.id,
                entry_kind='directory' if existing.is_dir else 'file',
                entry=existing,
                from_parent_id=restored_from_parent_id,
                to_parent_id=existing.parent_id,
                updated_at=existing.updated_at,
            )
        return {
            "mode": "instant",
            "restored_from_recycle": restored_from_recycle,
            "file": build_uploaded_file_payload(existing, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
        }

    target_dir = get_user_upload_root(user) if not target_parent else (get_upload_root() / Path(target_parent.relative_path))
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = build_stored_name(display_name)
    target_path = target_dir / stored_name
    with target_path.open("wb") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

    next_relative_path = relative_to_uploads(target_path)
    file_record = UploadedFile.objects.create(
        file_md5=file_md5,
        file_size=int(file_obj.size),
        relative_path=next_relative_path,
        created_by=user,
        parent=target_parent,
        is_dir=False,
        stored_name=stored_name,
        display_name=display_name,
        business=category,
    )
    asset, _ = ensure_asset_compat_for_uploaded_file(file_record)
    ensure_audio_asset_pipeline(asset)
    ensure_video_asset_pipeline(asset)
    notify_resource_entry_created(file_record)

    return {
        "mode": "direct",
        "file": build_uploaded_file_payload(file_record, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
    }


def precheck_file_upload(*, user, file_md5: str, file_name: str, file_size: int, parent_id: int | None, relative_path: str) -> dict:
    _validate_upload_file_size(int(file_size or 0))

    parent = get_parent_dir(user, parent_id)
    if parent_id is not None and parent is None:
        raise ValidationError({"detail": "目标目录不存在"})

    nested_folders, display_name = split_relative_upload_path(relative_path, file_name)
    target_parent = ensure_nested_parent(user, parent, nested_folders)

    existing, restored_from_recycle, restored_from_parent_id = resolve_existing_upload_file(user, file_md5, target_parent, display_name)
    if existing:
        if restored_from_recycle:
            notify_resource_entry_moved(
                owner_user_id=existing.created_by_id,
                entry_id=existing.id,
                entry_kind='directory' if existing.is_dir else 'file',
                entry=existing,
                from_parent_id=restored_from_parent_id,
                to_parent_id=existing.parent_id,
                updated_at=existing.updated_at,
            )
        return {
            "exists": True,
            "message": "文件已在回收站中恢复" if restored_from_recycle else "文件已存在，秒传成功",
            "restored_from_recycle": restored_from_recycle,
            "file": build_uploaded_file_payload(existing, entry_is_within_recycle_bin_tree=entry_is_within_recycle_bin_tree),
        }

    return {
        "exists": False,
        "message": "文件不存在，开始分片上传",
    }


def store_upload_chunk(*, user, file_md5: str, chunk_index: int, chunk_md5: str, file_obj) -> dict:
    temp_dir = get_temp_root() / f"{user.id}_{file_md5}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = temp_dir / str(chunk_index)

    with chunk_path.open("wb") as f:
        for part in file_obj.chunks():
            f.write(part)

    if len(chunk_md5) == 32 and not verify_chunk_md5(chunk_path, chunk_md5):
        chunk_path.unlink(missing_ok=True)
        raise ValidationError({"detail": "分片MD5校验失败"})

    return {"chunk_index": chunk_index, "uploaded": True}


def submit_large_file_merge(*, user, file_md5: str, total_md5: str, file_name: str, total_chunks: int, file_size: int, parent_id: int | None, relative_path: str, category: str) -> dict:
    _validate_upload_file_size(int(file_size or 0))

    parent = get_parent_dir(user, parent_id)
    if parent_id is not None and parent is None:
        raise ValidationError({"detail": "目标目录不存在"})

    nested_folders, display_name = split_relative_upload_path(relative_path, file_name)
    target_parent = ensure_nested_parent(user, parent, nested_folders)

    temp_dir = get_temp_root() / f"{user.id}_{file_md5}"
    missing_chunks: list[int] = []
    for idx in range(1, total_chunks + 1):
        if not (temp_dir / str(idx)).exists():
            missing_chunks.append(idx)

    if missing_chunks:
        raise ValidationError({
            "detail": "分片未上传完整",
            "missing_chunks": missing_chunks,
        })

    try:
        inspector = celery_app.control.inspect(timeout=1)
        online_workers = inspector.ping() or {}
    except Exception:
        online_workers = {}

    if not online_workers:
        raise UploadMergeServiceUnavailableError(
            detail="后台合并服务不可用（未检测到 Celery Worker）",
            hint="请先启动 Celery Worker：celery -A hyself_server worker -l info",
        )

    task = merge_large_file_task.delay(
        file_md5=file_md5,
        total_chunks=total_chunks,
        file_name=file_name,
        display_name=display_name,
        total_md5=total_md5,
        file_size=file_size,
        user_id=user.id,
        parent_id=target_parent.id if target_parent else None,
        business=category,
    )
    return {"task_id": task.id, "message": "已提交后台合并任务"}
