from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.application.commands.attachments import execute_send_asset_message_command
from chat.application.commands.realtime import execute_send_text_message_command
from chat.domain.access import get_conversation_access
from chat.models import ChatConversation, ChatMessage


def _build_forwarded_from_payload(message: ChatMessage) -> dict:
    sender_name = "系统"
    if message.sender is not None:
        sender_name = message.sender.display_name or message.sender.username
    preview = message.content or ""
    if message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        preview = str((message.payload or {}).get("display_name") or message.content or "附件")
    return {
        "id": message.id,
        "sequence": message.sequence,
        "conversation_id": message.conversation_id,
        "message_type": message.message_type,
        "sender_name": sender_name,
        "content_preview": preview[:120],
    }


def execute_forward_messages_command(user, *, target_conversation_id: int, message_ids: list[int]) -> dict:
    if not message_ids:
        raise ValidationError({"message_ids": "至少选择一条消息"})

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

    forwarded_count = 0
    for source_message in selected_messages:
        source_access = get_conversation_access(user, source_message.conversation)
        if source_access.access_mode != "member":
            raise PermissionDenied("当前无权转发该消息")

        extra_payload = {"forwarded_from_message": _build_forwarded_from_payload(source_message)}
        if source_message.message_type == ChatMessage.MessageType.TEXT:
            execute_send_text_message_command(
                user,
                target_conversation.id,
                content=source_message.content,
                extra_payload=extra_payload,
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
                extra_payload=extra_payload,
                emit_events=True,
            )
            forwarded_count += 1

    return {
        "detail": "消息已转发",
        "target_conversation_id": target_conversation.id,
        "forwarded_count": forwarded_count,
    }