from __future__ import annotations

from typing import NotRequired, TypedDict, cast

from hyself.models import AssetReference
from chat.models import ChatMessage
from hyself.utils.upload import media_url


class ChatReplyPayload(TypedDict):
    id: int
    sequence: int
    message_type: str
    sender_name: str
    content_preview: str
    is_revoked: NotRequired[bool]


def is_message_revoked(message: ChatMessage) -> bool:
    revoked = (message.payload or {}).get("revoked")
    return isinstance(revoked, dict) and bool(revoked.get("revoked_at"))


class ChatAssetPayload(TypedDict):
    asset_reference_id: int
    source_asset_reference_id: int
    display_name: str
    media_type: str
    mime_type: str
    file_size: int | None
    url: str
    stream_url: NotRequired[str]
    thumbnail_url: NotRequired[str]
    processing_status: NotRequired[str]
    subtitle_tracks: NotRequired[list[dict[str, object]]]


class ChatRecordItemPayload(TypedDict):
    source_message_id: int
    sequence: int
    conversation_id: int
    message_type: str
    sender_name: str
    sender_avatar: str
    content: str
    asset: NotRequired[ChatAssetPayload]
    chat_record: NotRequired[ChatRecordPayload]


class ChatRecordPayload(TypedDict):
    version: int
    title: str
    footer_label: str
    items: list[ChatRecordItemPayload]


def _require_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(field_name)
    return value


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(field_name)
    return value


def resolve_message_sender_name(message: ChatMessage) -> str:
    if message.sender is None:
        return "系统"
    return message.sender.display_name or message.sender.username


def build_message_preview(message: ChatMessage) -> str:
    if is_message_revoked(message):
        return "引用的内容已撤回"
    preview = message.content or ""
    if message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        preview = str((message.payload or {}).get("display_name") or message.content or "附件")
    elif message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        preview = str(((message.payload or {}).get("chat_record") or {}).get("title") or message.content or "聊天记录")
    return preview[:120]


def build_reply_payload_from_message(message: ChatMessage) -> ChatReplyPayload:
    payload: ChatReplyPayload = {
        "id": message.id,
        "sequence": message.sequence,
        "message_type": message.message_type,
        "sender_name": resolve_message_sender_name(message),
        "content_preview": build_message_preview(message),
    }
    if is_message_revoked(message):
        payload["is_revoked"] = True
    return payload


def build_asset_payload(*, chat_reference: AssetReference, source_reference: AssetReference) -> ChatAssetPayload:
    asset = source_reference.asset
    if asset is None:
        raise ValueError("source_reference.asset")
    video_processing = ((asset.extra_metadata or {}).get("video_processing") if isinstance(asset.extra_metadata, dict) else None) or {}
    return {
        "asset_reference_id": chat_reference.id,
        "source_asset_reference_id": source_reference.id,
        "display_name": chat_reference.display_name,
        "media_type": asset.media_type,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "url": media_url(asset.storage_key) if asset.storage_key and asset.storage_backend == asset.StorageBackend.LOCAL else "",
        "stream_url": str(video_processing.get("playlist_url") or ""),
        "thumbnail_url": str(video_processing.get("thumbnail_url") or ""),
        "processing_status": str(video_processing.get("status") or ""),
        "subtitle_tracks": video_processing.get("subtitle_tracks") or [],
    }


def require_source_asset_reference_id(payload: object) -> int:
    if not isinstance(payload, dict):
        raise ValueError("source_asset_reference_id")
    raw_value = payload.get("source_asset_reference_id") or payload.get("asset_reference_id")
    if not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value <= 0:
        raise ValueError("source_asset_reference_id")
    return raw_value


def require_chat_record_payload(payload: object) -> ChatRecordPayload:
    if not isinstance(payload, dict):
        raise ValueError("chat_record")
    version = _require_int(payload.get("version"), "chat_record.version")
    title = _require_str(payload.get("title"), "chat_record.title")
    footer_label = _require_str(payload.get("footer_label"), "chat_record.footer_label")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("chat_record.items")

    items: list[ChatRecordItemPayload] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("chat_record.items")
        item: ChatRecordItemPayload = {
            "source_message_id": _require_int(raw_item.get("source_message_id"), "chat_record.items.source_message_id"),
            "sequence": _require_int(raw_item.get("sequence"), "chat_record.items.sequence"),
            "conversation_id": _require_int(raw_item.get("conversation_id"), "chat_record.items.conversation_id"),
            "message_type": _require_str(raw_item.get("message_type"), "chat_record.items.message_type"),
            "sender_name": _require_str(raw_item.get("sender_name"), "chat_record.items.sender_name"),
            "sender_avatar": _require_str(raw_item.get("sender_avatar"), "chat_record.items.sender_avatar"),
            "content": _require_str(raw_item.get("content"), "chat_record.items.content"),
        }
        if "asset" in raw_item and raw_item.get("asset") is not None:
            asset_payload = raw_item.get("asset")
            if not isinstance(asset_payload, dict):
                raise ValueError("chat_record.items.asset")
            item["asset"] = {
                "asset_reference_id": _require_int(asset_payload.get("asset_reference_id"), "chat_record.items.asset.asset_reference_id"),
                "source_asset_reference_id": _require_int(asset_payload.get("source_asset_reference_id"), "chat_record.items.asset.source_asset_reference_id"),
                "display_name": _require_str(asset_payload.get("display_name"), "chat_record.items.asset.display_name"),
                "media_type": _require_str(asset_payload.get("media_type"), "chat_record.items.asset.media_type"),
                "mime_type": _require_str(asset_payload.get("mime_type") or "", "chat_record.items.asset.mime_type"),
                "file_size": cast(int | None, asset_payload.get("file_size") if asset_payload.get("file_size") is None or isinstance(asset_payload.get("file_size"), int) else (_ for _ in ()).throw(ValueError("chat_record.items.asset.file_size"))),
                "url": _require_str(asset_payload.get("url") or "", "chat_record.items.asset.url"),
            }
        if "chat_record" in raw_item and raw_item.get("chat_record") is not None:
            item["chat_record"] = require_chat_record_payload(raw_item.get("chat_record"))
        items.append(item)

    return {
        "version": version,
        "title": title,
        "footer_label": footer_label,
        "items": items,
    }