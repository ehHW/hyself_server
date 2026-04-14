from __future__ import annotations

import json
import re
import subprocess
import shutil
from pathlib import Path

from django.db import transaction

from hyself.models import Asset
from hyself.utils.upload import build_stored_name, get_upload_root, media_url, normalize_relative_path


VIDEO_PROCESSING_KEY = "video_processing"
VIDEO_ARTIFACTS_ROOT = "video_artifacts"
WINDOWS_RESERVED_NAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_artifact_name(value: str, *, fallback: str) -> str:
    candidate = WINDOWS_RESERVED_NAME_PATTERN.sub("_", str(value or "")).strip().strip(". ")
    candidate = re.sub(r"\s+", "_", candidate)
    return candidate[:96] or fallback


def _get_video_artifact_base_name(asset: Asset) -> str:
    original_stem = Path(str(asset.original_name or "")).stem
    return _sanitize_artifact_name(original_stem, fallback=f"video_{asset.id}")


def _get_video_artifact_storage_name(asset: Asset) -> str:
    metadata = dict(asset.extra_metadata or {})
    video_processing = dict(metadata.get(VIDEO_PROCESSING_KEY) or {})
    existing_name = str(video_processing.get("artifact_storage_name") or "").strip()
    if existing_name:
        return _sanitize_artifact_name(existing_name, fallback=f"video_artifact_{asset.id}")
    generated_name = build_stored_name(asset.original_name or f"video_{asset.id}.mp4")
    generated_stem = Path(generated_name).stem
    return _sanitize_artifact_name(generated_stem, fallback=f"video_artifact_{asset.id}")


def _get_video_artifact_folder_name(asset: Asset) -> str:
    return f"{_get_video_artifact_storage_name(asset)}_{asset.id}"


def _build_subtitle_track_payload(*, index: int, codec_name: str, language: str, label: str, forced: bool, default: bool) -> dict:
    return {
        "index": index,
        "codec": codec_name,
        "language": language,
        "label": label,
        "forced": forced,
        "default": default,
    }


def _resolve_command_path(command_name: str) -> str:
    resolved = shutil.which(command_name)
    if resolved:
        return resolved
    raise FileNotFoundError(f"未找到 {command_name}，请先安装并加入 PATH")


def _run_json_command(command: list[str]) -> dict:
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout or "{}")


def _get_asset_file_path(asset: Asset) -> Path:
    return get_upload_root() / Path(normalize_relative_path(asset.storage_key))


def _get_video_output_root(asset: Asset, artifact_storage_name: str | None = None) -> Path:
    folder_name = f"{artifact_storage_name or _get_video_artifact_storage_name(asset)}_{asset.id}"
    target = get_upload_root() / Path(VIDEO_ARTIFACTS_ROOT) / folder_name
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
    ffprobe_path = _resolve_command_path("ffprobe")
    source_path = _get_asset_file_path(asset)
    if not source_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {source_path}")

    probe_result = _run_json_command([
        ffprobe_path,
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
    subtitle_streams: list[dict] = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        stream_index = _to_int(stream.get("index"))
        if stream_index is None:
            continue
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
        language = str(tags.get("language") or "und").strip().lower() or "und"
        title = str(tags.get("title") or "").strip()
        codec = str(stream.get("codec_name") or "").strip()
        subtitle_streams.append(
            _build_subtitle_track_payload(
                index=stream_index,
                codec_name=codec,
                language=language,
                label=title or language.upper(),
                forced=bool(disposition.get("forced")),
                default=bool(disposition.get("default")),
            )
        )

    return {
        "codec": codec_name,
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
        "subtitle_streams": subtitle_streams,
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
        "subtitle_streams": probe_payload.get("subtitle_streams") or [],
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
    from hyself.tasks import process_video_asset_task

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
    ffmpeg_path = _resolve_command_path("ffmpeg")
    source_path = _get_asset_file_path(asset)
    if not source_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {source_path}")

    artifact_display_name = _get_video_artifact_base_name(asset)
    artifact_storage_name = _get_video_artifact_storage_name(asset)
    output_root = _get_video_output_root(asset, artifact_storage_name)
    playlist_path = output_root / f"{artifact_storage_name}.m3u8"
    segment_pattern = output_root / f"{artifact_storage_name}_segment_%04d.ts"
    thumbnail_path = output_root / f"{artifact_storage_name}_cover.jpg"

    for stale_path in output_root.iterdir():
        if stale_path.is_file():
            stale_path.unlink(missing_ok=True)

    thumbnail_offset_seconds = 0.0
    if asset.duration_seconds and asset.duration_seconds > 0:
        thumbnail_offset_seconds = min(asset.duration_seconds * 0.2, 1.0)

    subprocess.run([
        ffmpeg_path,
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
        ffmpeg_path,
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

    video_processing = ((asset.extra_metadata or {}).get(VIDEO_PROCESSING_KEY) if isinstance(asset.extra_metadata, dict) else None) or {}
    subtitle_streams = video_processing.get("subtitle_streams") if isinstance(video_processing, dict) else []
    subtitle_tracks: list[dict] = []
    if isinstance(subtitle_streams, list):
        for order, stream in enumerate(subtitle_streams, start=1):
            if not isinstance(stream, dict):
                continue
            stream_index = _to_int(stream.get("index"))
            if stream_index is None:
                continue
            language = _sanitize_artifact_name(str(stream.get("language") or "und").lower(), fallback=f"sub_{order}")
            label = str(stream.get("label") or language.upper()).strip() or language.upper()
            subtitle_path = output_root / f"{artifact_storage_name}_subtitle_{language}_{order}.vtt"
            try:
                subprocess.run([
                    ffmpeg_path,
                    "-y",
                    "-i",
                    str(source_path),
                    "-map",
                    f"0:{stream_index}",
                    "-c:s",
                    "webvtt",
                    str(subtitle_path),
                ], check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError:
                continue
            subtitle_relative_path = subtitle_path.relative_to(get_upload_root()).as_posix()
            subtitle_tracks.append({
                "language": str(stream.get("language") or "und").strip().lower() or "und",
                "label": label,
                "path": subtitle_relative_path,
                "url": f"{media_url(subtitle_relative_path)}?v={subtitle_path.stat().st_mtime_ns}",
                "default": bool(stream.get("default")) and not any(item.get("default") for item in subtitle_tracks),
                "forced": bool(stream.get("forced")),
            })
    if subtitle_tracks and not any(item.get("default") for item in subtitle_tracks):
        subtitle_tracks[0]["default"] = True

    playlist_relative_path = playlist_path.relative_to(get_upload_root()).as_posix()
    thumbnail_relative_path = thumbnail_path.relative_to(get_upload_root()).as_posix()
    playlist_version = str(playlist_path.stat().st_mtime_ns)
    thumbnail_version = str(thumbnail_path.stat().st_mtime_ns)
    artifact_directory_path = output_root.relative_to(get_upload_root()).as_posix()

    return {
        "artifact_directory_name": output_root.name,
        "artifact_directory_path": artifact_directory_path,
        "artifact_display_name": artifact_display_name,
        "artifact_storage_name": artifact_storage_name,
        "playlist_path": playlist_relative_path,
        "playlist_url": f"{media_url(playlist_relative_path)}?v={playlist_version}",
        "thumbnail_path": thumbnail_relative_path,
        "thumbnail_url": f"{media_url(thumbnail_relative_path)}?v={thumbnail_version}",
        "subtitle_tracks": subtitle_tracks,
    }
