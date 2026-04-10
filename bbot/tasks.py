import shutil
from pathlib import Path

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

from bbot.asset_compat import ensure_asset_compat_for_uploaded_file
from bbot.models import UploadedFile
from bbot.video_processing import ensure_video_asset_pipeline, mark_video_processing_status, transcode_video_to_hls
from utils.upload import (
    build_stored_name,
    calc_file_md5,
    get_temp_root,
    get_upload_root,
    get_user_upload_root,
    media_url,
    relative_to_uploads,
)
from user.models import User


def _send_progress(task_id: str, payload: dict):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    merged_payload = {
        "type": "upload_progress",
        "task_id": task_id,
        **payload,
    }
    group_name = f"upload_task_{task_id}"
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "upload.progress",
            "payload": merged_payload,
        },
    )


@shared_task(bind=True)
def merge_large_file_task(
    self,
    file_md5: str,
    total_chunks: int,
    file_name: str,
    display_name: str,
    total_md5: str,
    file_size: int,
    user_id: int | None,
    parent_id: int | None,
    business: str = "",
):
    task_id = self.request.id
    temp_dir = get_temp_root() / f"{user_id}_{file_md5}"
    output_path: Path | None = None

    try:
        _send_progress(task_id, {"status": "started", "progress": 0, "message": "开始合并分片"})

        upload_user = User.objects.filter(id=user_id).first() if user_id else None
        parent = (
            UploadedFile.objects.filter(id=parent_id, created_by=upload_user, is_dir=True).first()
            if parent_id and upload_user
            else None
        )
        if upload_user is None:
            raise ValueError("上传用户不存在")

        output_dir = get_user_upload_root(upload_user) if not parent else (get_upload_root() / Path(parent.relative_path))
        output_dir.mkdir(parents=True, exist_ok=True)
        stored_name = build_stored_name(display_name or file_name)
        output_path = output_dir / stored_name

        with output_path.open("wb") as target:
            for idx in range(1, total_chunks + 1):
                chunk_path = temp_dir / str(idx)
                if not chunk_path.exists():
                    raise FileNotFoundError(f"缺少分片: {idx}")

                with chunk_path.open("rb") as src:
                    shutil.copyfileobj(src, target, length=1024 * 1024)

                progress = int((idx / total_chunks) * 90)
                _send_progress(
                    task_id,
                    {
                        "status": "merging",
                        "progress": progress,
                        "message": f"正在合并分片 {idx}/{total_chunks}",
                    },
                )

        _send_progress(task_id, {"status": "verifying", "progress": 95, "message": "开始校验文件MD5"})
        merged_md5 = calc_file_md5(output_path)
        if merged_md5 != total_md5:
            output_path.unlink(missing_ok=True)
            raise ValueError("合并后MD5校验失败")

        shutil.rmtree(temp_dir, ignore_errors=True)

        relative_path = relative_to_uploads(output_path)
        file_record = UploadedFile.objects.filter(
            created_by=upload_user,
            parent=parent,
            display_name=(display_name or file_name),
            is_dir=False,
        ).first()
        if file_record:
            file_record.stored_name = stored_name
            file_record.display_name = display_name or file_name
            file_record.file_size = file_size
            file_record.relative_path = relative_path
            file_record.file_md5 = file_md5
            file_record.business = business
            file_record.save(
                update_fields=[
                    "stored_name",
                    "display_name",
                    "file_size",
                    "relative_path",
                    "file_md5",
                    "business",
                    "updated_at",
                ]
            )
        else:
            file_record = UploadedFile.objects.create(
                stored_name=stored_name,
                display_name=display_name or file_name,
                file_md5=file_md5,
                file_size=file_size,
                relative_path=relative_path,
                created_by=upload_user,
                parent=parent,
                is_dir=False,
                business=business,
            )
        asset, asset_reference = ensure_asset_compat_for_uploaded_file(file_record)
        ensure_video_asset_pipeline(asset)

        result_payload = {
            "status": "done",
            "progress": 100,
            "message": "合并完成",
            "file_md5": file_md5,
            "relative_path": relative_path,
            "url": media_url(relative_path),
            "asset_reference_id": asset_reference.id,
            "file": {
                "id": file_record.id,
                "display_name": file_record.display_name,
                "relative_path": file_record.relative_path,
                "url": media_url(relative_path),
                "asset_reference_id": asset_reference.id,
            },
        }
        _send_progress(task_id, result_payload)
        return result_payload
    except Exception as exc:
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        shutil.rmtree(temp_dir, ignore_errors=True)

        failed_payload = {
            "status": "failed",
            "progress": 100,
            "message": str(exc),
            "file_md5": file_md5,
        }
        _send_progress(
            task_id,
            failed_payload,
        )
        return failed_payload


@shared_task(bind=True)
def process_video_asset_task(self, asset_id: int):
    asset = None
    try:
        from bbot.models import Asset as AssetModel

        asset = AssetModel.objects.filter(id=asset_id, deleted_at__isnull=True).first()
        if asset is None:
            return {"status": "missing", "asset_id": asset_id}

        mark_video_processing_status(asset, status="processing")
        outputs = transcode_video_to_hls(asset)
        mark_video_processing_status(asset, status="ready", extra=outputs)
        return {"status": "ready", "asset_id": asset.id, **outputs}
    except Exception as exc:
        if asset is not None:
            mark_video_processing_status(asset, status="failed", error=str(exc))
        return {"status": "failed", "asset_id": asset_id, "error": str(exc)}
