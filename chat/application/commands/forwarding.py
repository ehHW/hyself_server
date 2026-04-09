from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.application.commands.attachments import execute_send_asset_message_command
from chat.application.commands.realtime import execute_send_text_message_command
from chat.domain.messaging import create_message, get_total_unread_count
from chat.domain.access import get_conversation_access
from chat.domain.serialization import serialize_conversation, serialize_message
from chat.models import ChatConversation, ChatConversationMember, ChatMessage
from ws.events import notify_chat_conversation_updated, notify_chat_new_message, notify_chat_unread_updated


def _resolve_sender_name(message: ChatMessage) -> str:
    sender_name = "系统"
    if message.sender is not None:
        sender_name = message.sender.display_name or message.sender.username
    return sender_name


def _build_message_preview(message: ChatMessage) -> str:
    preview = message.content or ""
    if message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        preview = str((message.payload or {}).get("display_name") or message.content or "附件")
    elif message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        preview = str(((message.payload or {}).get("chat_record") or {}).get("title") or message.content or "聊天记录")
    return preview[:120]


def _serialize_chat_record_item(message: ChatMessage) -> dict:
    payload = message.payload or {}
    item = {
        "source_message_id": message.id,
        "sequence": message.sequence,
        "conversation_id": message.conversation_id,
        "message_type": message.message_type,
        "sender_name": _resolve_sender_name(message),
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
        sender_name = _resolve_sender_name(message)
        if sender_name not in sender_names:
            sender_names.append(sender_name)
    if not sender_names:
        return "聊天记录"
    if len(sender_names) == 1:
        return f"{sender_names[0]}的聊天记录"
    if len(sender_names) == 2:
        return f"{sender_names[0]}和{sender_names[1]}的聊天记录"
    return f"{sender_names[0]}和{sender_names[1]}等的聊天记录"


def _build_chat_record_payload(selected_messages: list[ChatMessage]) -> dict:
    return {
        "version": 1,
        "title": _build_merged_record_title(selected_messages),
        "footer_label": "聊天记录",
        "items": [_serialize_chat_record_item(message) for message in selected_messages],
    }


def _emit_message_events(*, user, conversation: ChatConversation, message: ChatMessage) -> None:
    conversation = ChatConversation.objects.select_related("owner", "group_config").get(pk=conversation.pk)
    message_payload = serialize_message(message)
    sender_conversation = serialize_conversation(conversation, user)
    recipient_members = list(
        ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).exclude(user_id=user.id)
    )
    hidden_recipient_ids = [item.pk for item in recipient_members if not item.show_in_list]
    if hidden_recipient_ids:
        ChatConversationMember.objects.filter(pk__in=hidden_recipient_ids).update(show_in_list=True)
    recipient_payloads = []
    for recipient_member in recipient_members:
        refreshed_member = ChatConversationMember.objects.get(pk=recipient_member.pk)
        recipient_payloads.append(
            {
                "user_id": recipient_member.user_id,
                "conversation": serialize_conversation(conversation, recipient_member.user),
                "unread_count": refreshed_member.unread_count,
                "total_unread_count": get_total_unread_count(recipient_member.user),
            }
        )

    notify_chat_new_message(user.id, {"conversation_id": conversation.id, "message": message_payload})
    notify_chat_conversation_updated(user.id, sender_conversation)
    for recipient in recipient_payloads:
        notify_chat_new_message(recipient["user_id"], {"conversation_id": conversation.id, "message": message_payload})
        notify_chat_conversation_updated(recipient["user_id"], recipient["conversation"])
        notify_chat_unread_updated(recipient["user_id"], conversation.id, recipient["unread_count"], recipient["total_unread_count"])


def _send_chat_record_message(user, *, target_conversation: ChatConversation, chat_record_payload: dict, emit_events: bool = True) -> ChatMessage:
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

    target_conversation = ChatConversation.objects.select_related("owner", "group_config").filter(
        id=target_conversation_id,
        status=ChatConversation.Status.ACTIVE,
    ).first()
    if target_conversation is None:
        raise ValidationError({"target_conversation_id": "目标会话不存在"})

    target_access = get_conversation_access(user, target_conversation)
    if target_access.access_mode != "member" or not target_access.can_send_message:
        raise PermissionDenied("当前无权向目标会话转发消息")

    selected_messages = list(
        ChatMessage.objects.select_related("conversation", "sender")
        .filter(id__in=message_ids, is_system=False)
        .order_by("created_at", "sequence", "id")
    )
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
            source_payload = source_message.payload or {}
            source_asset_reference_id = int(source_payload.get("source_asset_reference_id") or source_payload.get("asset_reference_id") or 0)
            if not source_asset_reference_id:
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
            chat_record_payload = (source_message.payload or {}).get("chat_record")
            if not isinstance(chat_record_payload, dict):
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