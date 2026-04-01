from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from bbot.models import UploadedFile
from utils.upload import get_upload_root, get_user_relative_root, join_relative_path

RECYCLE_BIN_TAG = "recycle_bin"
RECYCLE_BIN_DISPLAY_NAME = "回收站"
RECYCLE_BIN_STORED_NAME = "__recycle_bin__"
RECYCLE_BIN_EXPIRE_DAYS = 30


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip())
    return cleaned.strip("._") or "item"


def _split_name_suffix(name: str) -> tuple[str, str]:
    dot_index = str(name).rfind(".")
    if dot_index <= 0:
        return str(name), ""
    return str(name)[:dot_index], str(name)[dot_index:]


def _resolve_restore_name(entry: UploadedFile, parent: UploadedFile | None, desired_name: str) -> str:
    current_name = str(desired_name or entry.display_name or "未命名")
    base_name, suffix = _split_name_suffix(current_name)
    candidate = current_name
    index = 1
    while UploadedFile.objects.filter(
        created_by=entry.created_by,
        parent=parent,
        display_name=candidate,
        deleted_at__isnull=True,
    ).exclude(id=entry.id).exists():
        candidate = f"{base_name}({index}){suffix}"
        index += 1
    return candidate


def _collect_subtree(entry: UploadedFile) -> list[UploadedFile]:
    subtree = [entry]
    cursor = [entry.id]
    while cursor:
        children = list(UploadedFile.all_objects.filter(parent_id__in=cursor, deleted_at__isnull=True).order_by("id"))
        if not children:
            break
        subtree.extend(children)
        cursor = [item.id for item in children]
    return subtree


def _purge_entry_tree(entry: UploadedFile) -> tuple[int, int, int]:
    subtree = _collect_subtree(entry)
    files = [item for item in subtree if not item.is_dir]
    dirs = sorted((item for item in subtree if item.is_dir), key=lambda item: len(item.relative_path or ""), reverse=True)

    removed_db_files = 0
    removed_db_dirs = 0
    removed_disk_files = 0

    for file_item in files:
        should_delete_disk = not UploadedFile.all_objects.filter(
            relative_path=file_item.relative_path,
            is_dir=False,
            deleted_at__isnull=True,
        ).exclude(id=file_item.id).exists()

        if should_delete_disk and file_item.relative_path:
            file_path = get_upload_root() / Path(file_item.relative_path)
            if file_path.exists() and file_path.is_file():
                file_path.unlink(missing_ok=True)
                removed_disk_files += 1

        UploadedFile.all_objects.filter(id=file_item.id).hard_delete()
        removed_db_files += 1

    for dir_item in dirs:
        if dir_item.relative_path:
            dir_path = get_upload_root() / Path(dir_item.relative_path)
            if dir_path.exists() and dir_path.is_dir():
                try:
                    dir_path.rmdir()
                except OSError:
                    pass
        UploadedFile.all_objects.filter(id=dir_item.id).hard_delete()
        removed_db_dirs += 1

    return removed_db_files, removed_db_dirs, removed_disk_files


def serialize_recycle_entry(item: UploadedFile) -> dict:
    recycled_at = item.recycled_at
    expires_at = recycled_at + timedelta(days=RECYCLE_BIN_EXPIRE_DAYS) if recycled_at else None
    remaining_days = None
    if expires_at:
        remaining_days = max(0, (expires_at.date() - timezone.now().date()).days)

    return {
        "id": item.id,
        "display_name": item.display_name,
        "stored_name": item.stored_name,
        "is_dir": item.is_dir,
        "parent_id": item.parent_id,
        "file_size": item.file_size,
        "file_md5": item.file_md5,
        "relative_path": item.relative_path,
        "url": "" if item.is_dir or not item.relative_path else f"/uploads/{item.relative_path}",
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "is_system": item.is_system,
        "is_recycle_bin": is_recycle_bin_folder(item),
        "recycled_at": recycled_at,
        "expires_at": expires_at,
        "remaining_days": remaining_days,
        "recycle_original_parent_id": item.recycle_original_parent_id,
    }


def is_recycle_bin_folder(entry: UploadedFile) -> bool:
    return bool(entry.is_dir and entry.is_system and entry.business == RECYCLE_BIN_TAG)


def ensure_user_recycle_bin(user) -> UploadedFile:
    user_relative_root = get_user_relative_root(user)
    recycle_relative_path = join_relative_path(user_relative_root, RECYCLE_BIN_STORED_NAME)

    (get_upload_root() / Path(recycle_relative_path)).mkdir(parents=True, exist_ok=True)

    recycle_bin = UploadedFile.all_objects.filter(
        created_by=user,
        is_dir=True,
        business=RECYCLE_BIN_TAG,
        parent__isnull=True,
    ).first()

    if recycle_bin:
        recycle_bin.deleted_at = None
        recycle_bin.display_name = RECYCLE_BIN_DISPLAY_NAME
        recycle_bin.stored_name = RECYCLE_BIN_STORED_NAME
        recycle_bin.relative_path = recycle_relative_path
        recycle_bin.is_system = True
        recycle_bin.recycled_at = None
        recycle_bin.recycle_original_parent = None
        recycle_bin.save(
            update_fields=[
                "deleted_at",
                "display_name",
                "stored_name",
                "relative_path",
                "is_system",
                "recycled_at",
                "recycle_original_parent",
                "updated_at",
            ]
        )
        return recycle_bin

    return UploadedFile.objects.create(
        created_by=user,
        parent=None,
        is_dir=True,
        display_name=RECYCLE_BIN_DISPLAY_NAME,
        stored_name=RECYCLE_BIN_STORED_NAME,
        relative_path=recycle_relative_path,
        file_size=0,
        file_md5="",
        is_system=True,
        business=RECYCLE_BIN_TAG,
        recycled_at=None,
        recycle_original_parent=None,
    )


def move_entry_to_recycle_bin(entry: UploadedFile) -> int:
    if is_recycle_bin_folder(entry):
        raise ValueError("回收站不可删除")

    recycle_bin = ensure_user_recycle_bin(entry.created_by)
    now = timezone.now()
    original_parent = entry.parent

    subtree_ids = [item.id for item in _collect_subtree(entry)]

    UploadedFile.all_objects.filter(id__in=subtree_ids, deleted_at__isnull=True).update(recycled_at=now, updated_at=now)

    entry.parent = recycle_bin
    entry.recycled_at = now
    entry.recycle_original_parent = original_parent
    entry.save(update_fields=["parent", "recycled_at", "recycle_original_parent", "updated_at"])

    return len(subtree_ids)


def restore_entry_from_recycle_bin(entry: UploadedFile) -> UploadedFile:
    if entry.deleted_at is not None:
        raise ValueError("文件已删除")
    if entry.recycled_at is None:
        raise ValueError("该文件不在回收站中")
    if is_recycle_bin_folder(entry):
        raise ValueError("回收站目录不可还原")

    target_parent = entry.recycle_original_parent
    if target_parent and target_parent.deleted_at is not None:
        target_parent = None

    entry.display_name = _resolve_restore_name(entry, target_parent, entry.display_name)
    entry.parent = target_parent
    entry.recycled_at = None
    entry.recycle_original_parent = None
    entry.save(update_fields=["display_name", "parent", "recycled_at", "recycle_original_parent", "updated_at"])

    UploadedFile.all_objects.filter(parent=entry, deleted_at__isnull=True).update(updated_at=timezone.now())
    return entry


def list_recycle_bin_entries(user) -> list[dict]:
    recycle_bin = ensure_user_recycle_bin(user)
    items = list(
        UploadedFile.objects.filter(created_by=user, parent=recycle_bin)
        .order_by("recycled_at", "display_name", "id")
    )
    serialized = [serialize_recycle_entry(item) for item in items]
    return sorted(serialized, key=lambda item: (item["remaining_days"] if item["remaining_days"] is not None else 999999, item["display_name"]))


def clear_recycle_bin(user, entry_ids: list[int] | None = None) -> dict[str, int]:
    recycle_bin = ensure_user_recycle_bin(user)
    query = UploadedFile.objects.filter(created_by=user, parent=recycle_bin)
    if entry_ids:
        query = query.filter(id__in=entry_ids)

    removed_db_files = 0
    removed_db_dirs = 0
    removed_disk_files = 0

    for entry in list(query.order_by("id")):
        file_count, dir_count, disk_count = _purge_entry_tree(entry)
        removed_db_files += file_count
        removed_db_dirs += dir_count
        removed_disk_files += disk_count

    return {
        "removed_db_files": removed_db_files,
        "removed_db_dirs": removed_db_dirs,
        "removed_disk_files": removed_disk_files,
    }


def cleanup_expired_recycle_bin(days: int = 30) -> dict[str, int]:
    cutoff = timezone.now() - timedelta(days=days)

    expired_entries = list(
        UploadedFile.objects.filter(parent__business=RECYCLE_BIN_TAG, recycled_at__isnull=False, recycled_at__lte=cutoff).order_by("id")
    )

    removed_db_files = 0
    removed_db_dirs = 0
    removed_disk_files = 0

    for entry in expired_entries:
        file_count, dir_count, disk_count = _purge_entry_tree(entry)
        removed_db_files += file_count
        removed_db_dirs += dir_count
        removed_disk_files += disk_count

    return {
        "removed_db_files": removed_db_files,
        "removed_db_dirs": removed_db_dirs,
        "removed_disk_files": removed_disk_files,
    }
