from __future__ import annotations

import json
import subprocess
from pathlib import Path

from django.db import transaction

from bbot.models import Asset
from utils.upload import get_upload_root, media_url, normalize_relative_path


VIDEO_PROCESSING_KEY = "video_processing"


def _run_json_command(command: list[str]) -> dict:
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout or "{}")


def _get_asset_file_path(asset: Asset) -> Path:
    return get_upload_root() / Path(normalize_relative_path(asset.storage_key))


def _get_video_output_root(asset: Asset) -> Path:
    target = get_upload_root() / Path("video_artifacts") / str(asset.id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_video_asset(asset: Asset) -> dict:
    source_path = _get_asset_file_path(asset)
    if not source_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {source_path}")

    probe_result = _run_json_command([
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(source_path),
    ])

    streams = probe_result.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    format_info = probe_result.get("format") or {}
    duration_seconds = _to_float((video_stream or {}).get("duration") or format_info.get("duration"))
    width = _to_int((video_stream or {}).get("width"))
    height = _to_int((video_stream or {}).get("height"))
    codec_name = str((video_stream or {}).get("codec_name") or "").strip()

    return {
        "codec": codec_name,
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
        "probe_raw": {
            "format_name": format_info.get("format_name"),
            "bit_rate": format_info.get("bit_rate"),
            "video_codec": codec_name,
        },
    }


@transaction.atomic
def update_asset_probe_metadata(asset: Asset, probe_payload: dict) -> Asset:
    metadata = dict(asset.extra_metadata or {})
    video_processing = dict(metadata.get(VIDEO_PROCESSING_KEY) or {})
    video_processing.update({
        "codec": probe_payload.get("codec") or "",
        "status": video_processing.get("status") or "queued",
        "duration_seconds": probe_payload.get("duration_seconds"),
        "width": probe_payload.get("width"),
        "height": probe_payload.get("height"),
        "probe_raw": probe_payload.get("probe_raw") or {},
    })
    metadata[VIDEO_PROCESSING_KEY] = video_processing

    asset.duration_seconds = probe_payload.get("duration_seconds") or asset.duration_seconds
    asset.width = probe_payload.get("width") or asset.width
    asset.height = probe_payload.get("height") or asset.height
    asset.extra_metadata = metadata
    asset.save(update_fields=["duration_seconds", "width", "height", "extra_metadata", "updated_at"])
    return asset


@transaction.atomic
def mark_video_processing_status(asset: Asset, *, status: str, error: str = "", extra: dict | None = None) -> Asset:
    metadata = dict(asset.extra_metadata or {})
    video_processing = dict(metadata.get(VIDEO_PROCESSING_KEY) or {})
    video_processing["status"] = status
    if error:
        video_processing["error"] = error
    elif "error" in video_processing:
        video_processing.pop("error", None)
    if extra:
        video_processing.update(extra)
    metadata[VIDEO_PROCESSING_KEY] = video_processing
    asset.extra_metadata = metadata
    asset.save(update_fields=["extra_metadata", "updated_at"])
    return asset


def queue_video_processing(asset: Asset) -> None:
    from bbot.tasks import process_video_asset_task

    metadata = dict(asset.extra_metadata or {})
    video_processing = dict(metadata.get(VIDEO_PROCESSING_KEY) or {})
    if video_processing.get("status") in {"queued", "processing", "ready"}:
        return
    mark_video_processing_status(asset, status="queued")
    process_video_asset_task.delay(asset.id)


def ensure_video_asset_pipeline(asset: Asset | None) -> Asset | None:
    if asset is None or asset.media_type != Asset.MediaType.VIDEO or asset.storage_backend != Asset.StorageBackend.LOCAL:
        return asset
    try:
        probe_payload = probe_video_asset(asset)
        asset = update_asset_probe_metadata(asset, probe_payload)
        queue_video_processing(asset)
    except Exception as exc:
        mark_video_processing_status(asset, status="failed", error=str(exc))
    return asset


def transcode_video_to_hls(asset: Asset) -> dict:
    source_path = _get_asset_file_path(asset)
    if not source_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {source_path}")

    output_root = _get_video_output_root(asset)
    playlist_path = output_root / "output.m3u8"
    segment_pattern = output_root / "segment_%04d.ts"
    thumbnail_path = output_root / "thumbnail.jpg"

    for stale_path in output_root.iterdir():
        if stale_path.is_file():
            stale_path.unlink(missing_ok=True)

    thumbnail_offset_seconds = 0.0
    if asset.duration_seconds and asset.duration_seconds > 0:
        thumbnail_offset_seconds = min(asset.duration_seconds * 0.2, 1.0)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-ss",
        f"{thumbnail_offset_seconds:.3f}",
        "-vf",
        "scale='min(640,iw)':-2",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(thumbnail_path),
    ], check=True, capture_output=True, text=True)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-force_key_frames",
        "expr:gte(t,n_forced*6)",
        "-hls_flags",
        "independent_segments",
        "-f",
        "hls",
        "-hls_time",
        "6",
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        str(segment_pattern),
        str(playlist_path),
    ], check=True, capture_output=True, text=True)

    playlist_relative_path = playlist_path.relative_to(get_upload_root()).as_posix()
    thumbnail_relative_path = thumbnail_path.relative_to(get_upload_root()).as_posix()
    playlist_version = str(playlist_path.stat().st_mtime_ns)
    thumbnail_version = str(thumbnail_path.stat().st_mtime_ns)

    return {
        "playlist_path": playlist_relative_path,
        "playlist_url": f"{media_url(playlist_relative_path)}?v={playlist_version}",
        "thumbnail_path": thumbnail_relative_path,
        "thumbnail_url": f"{media_url(thumbnail_relative_path)}?v={thumbnail_version}",
    }
