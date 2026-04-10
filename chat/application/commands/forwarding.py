from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.application.commands.attachments import execute_send_asset_message_command
from chat.application.commands.delivery import build_message_delivery_payloads, emit_message_delivery_events
from chat.application.commands.message_payloads import ChatRecordItemPayload, ChatRecordPayload, build_message_preview, require_chat_record_payload, require_source_asset_reference_id, resolve_message_sender_name
from chat.application.commands.realtime import execute_send_text_message_command
from chat.domain.messaging import create_message, get_total_unread_count
from chat.domain.access import get_conversation_access
from chat.infrastructure.repositories import get_active_conversation, list_forwardable_messages
from chat.models import ChatConversation, ChatMessage


def _serialize_chat_record_item(message: ChatMessage) -> ChatRecordItemPayload:
    payload = message.payload or {}
    item = {
        "source_message_id": message.id,
        "sequence": message.sequence,
        "conversation_id": message.conversation_id,
        "message_type": message.message_type,
        "sender_name": resolve_message_sender_name(message),
        "sender_avatar": "" if message.sender is None else (message.sender.avatar or ""),
        "content": message.content or "",
    }
    if message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        item["asset"] = {
            "asset_reference_id": payload.get("asset_reference_id"),
            "source_asset_reference_id": payload.get("source_asset_reference_id"),
            "display_name": payload.get("display_name") or message.content or "附件",
            "media_type": payload.get("media_type") or message.message_type,
            "mime_type": payload.get("mime_type") or "",
            "file_size": payload.get("file_size"),
            "url": payload.get("url") or "",
        }
    elif message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        nested_record = payload.get("chat_record")
        if not isinstance(nested_record, dict):
            raise ValidationError({"message_ids": "聊天记录消息缺少可用内容"})
        item["chat_record"] = nested_record
    return item


def _build_merged_record_title(selected_messages: list[ChatMessage]) -> str:
    source_conversation = selected_messages[0].conversation
    if source_conversation.type == ChatConversation.Type.GROUP:
        return "群聊的聊天记录"

    sender_names: list[str] = []
    for message in selected_messages:
        sender_name = resolve_message_sender_name(message)
        if sender_name not in sender_names:
            sender_names.append(sender_name)
    if not sender_names:
        return "聊天记录"
    if len(sender_names) == 1:
        return f"{sender_names[0]}的聊天记录"
    if len(sender_names) == 2:
        return f"{sender_names[0]}和{sender_names[1]}的聊天记录"
    return f"{sender_names[0]}和{sender_names[1]}等的聊天记录"


def _build_chat_record_payload(selected_messages: list[ChatMessage]) -> ChatRecordPayload:
    return {
        "version": 1,
        "title": _build_merged_record_title(selected_messages),
        "footer_label": "聊天记录",
        "items": [_serialize_chat_record_item(message) for message in selected_messages],
    }


def _emit_message_events(*, user, conversation: ChatConversation, message: ChatMessage) -> None:
    conversation, message_payload, sender_conversation, recipient_payloads = build_message_delivery_payloads(
        conversation=conversation,
        sender_user=user,
        message=message,
    )
    emit_message_delivery_events(
        conversation=conversation,
        sender_user=user,
        message_payload=message_payload,
        sender_conversation=sender_conversation,
        recipient_payloads=recipient_payloads,
    )


def _send_chat_record_message(user, *, target_conversation: ChatConversation, chat_record_payload: ChatRecordPayload, emit_events: bool = True) -> ChatMessage:
    access = get_conversation_access(user, target_conversation)
    if access.access_mode != "member" or not access.can_send_message:
        raise PermissionDenied("当前无权向目标会话转发消息")
    if access.member is not None and not access.member.show_in_list:
        access.member.show_in_list = True
        access.member.save(update_fields=["show_in_list", "updated_at"])
    message = create_message(
        target_conversation,
        user,
        str(chat_record_payload.get("title") or "聊天记录"),
        message_type=ChatMessage.MessageType.CHAT_RECORD,
        payload={"chat_record": chat_record_payload},
    )
    if emit_events:
        _emit_message_events(user=user, conversation=target_conversation, message=message)
    return message


def execute_forward_messages_command(user, *, target_conversation_id: int, message_ids: list[int], forward_mode: str = "separate") -> dict:
    if not message_ids:
        raise ValidationError({"message_ids": "至少选择一条消息"})
    if forward_mode not in {"separate", "merged"}:
        raise ValidationError({"forward_mode": "转发方式不支持"})

    target_conversation = get_active_conversation(target_conversation_id)
    if target_conversation is None:
        raise ValidationError({"target_conversation_id": "目标会话不存在"})

    target_access = get_conversation_access(user, target_conversation)
    if target_access.access_mode != "member" or not target_access.can_send_message:
        raise PermissionDenied("当前无权向目标会话转发消息")

    selected_messages = list_forwardable_messages(message_ids)
    if not selected_messages:
        raise ValidationError({"message_ids": "未找到可转发消息"})
    if len(selected_messages) != len(set(message_ids)):
        raise ValidationError({"message_ids": "部分消息不存在或不可转发"})

    for source_message in selected_messages:
        source_access = get_conversation_access(user, source_message.conversation)
        if source_access.access_mode != "member":
            raise PermissionDenied("当前无权转发该消息")

    if forward_mode == "merged":
        if len(selected_messages) < 2:
            raise ValidationError({"forward_mode": "合并转发至少需要两条消息"})
        merged_payload = _build_chat_record_payload(selected_messages)
        _send_chat_record_message(user, target_conversation=target_conversation, chat_record_payload=merged_payload, emit_events=True)
        return {
            "detail": "消息已转发",
            "target_conversation_id": target_conversation.id,
            "forwarded_count": len(selected_messages),
            "forward_mode": "merged",
        }

    forwarded_count = 0
    for source_message in selected_messages:
        if source_message.message_type == ChatMessage.MessageType.TEXT:
            execute_send_text_message_command(
                user,
                target_conversation.id,
                content=source_message.content,
                emit_events=True,
            )
            forwarded_count += 1
            continue

        if source_message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
            try:
                source_asset_reference_id = require_source_asset_reference_id(source_message.payload or {})
            except ValueError:
                raise ValidationError({"message_ids": "附件消息缺少可转发的资产引用"})
            execute_send_asset_message_command(
                user,
                target_conversation.id,
                source_asset_reference_id=source_asset_reference_id,
                emit_events=True,
            )
            forwarded_count += 1
            continue

        if source_message.message_type == ChatMessage.MessageType.CHAT_RECORD:
            try:
                chat_record_payload = require_chat_record_payload((source_message.payload or {}).get("chat_record"))
            except ValueError:
                raise ValidationError({"message_ids": "聊天记录消息缺少可转发内容"})
            _send_chat_record_message(user, target_conversation=target_conversation, chat_record_payload=chat_record_payload, emit_events=True)
            forwarded_count += 1
            continue

        raise ValidationError({"message_ids": f"暂不支持转发消息类型: {source_message.message_type}"})

    return {
        "detail": "消息已转发",
        "target_conversation_id": target_conversation.id,
        "forwarded_count": forwarded_count,
        "forward_mode": "separate",
    }