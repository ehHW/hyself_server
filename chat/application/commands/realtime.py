from __future__ import annotations

from ws.events import notify_chat_conversation_updated, notify_chat_new_message, notify_chat_unread_updated
from chat.domain.access import get_conversation_access
from chat.domain.messaging import create_message, get_total_unread_count, mark_conversation_read
from chat.domain.serialization import serialize_conversation, serialize_message
from chat.models import ChatConversation, ChatConversationMember, ChatMessage
from rest_framework.exceptions import PermissionDenied, ValidationError


def _build_reply_payload(conversation: ChatConversation, quoted_message_id: int | None) -> dict | None:
    if not quoted_message_id:
        return None
    quoted_message = ChatMessage.objects.select_related("sender").filter(conversation=conversation, id=quoted_message_id).first()
    if quoted_message is None:
        raise ValidationError({"quoted_message_id": "引用消息不存在"})
    sender_name = "系统"
    if quoted_message.sender is not None:
        sender_name = quoted_message.sender.display_name or quoted_message.sender.username
    preview = quoted_message.content or ""
    if quoted_message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        preview = str((quoted_message.payload or {}).get("display_name") or quoted_message.content or "附件")
    elif quoted_message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        preview = str(((quoted_message.payload or {}).get("chat_record") or {}).get("title") or quoted_message.content or "聊天记录")
    return {
        "id": quoted_message.id,
        "sequence": quoted_message.sequence,
        "message_type": quoted_message.message_type,
        "sender_name": sender_name,
        "content_preview": preview[:120],
    }


def execute_send_text_message_command(
    user,
    conversation_id: int,
    *,
    content: str,
    client_message_id: str | None = None,
    quoted_message_id: int | None = None,
    extra_payload: dict | None = None,
    emit_events: bool = False,
) -> dict:
    conversation = ChatConversation.objects.select_related("owner", "group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    access = get_conversation_access(user, conversation)
    if access.access_mode != "member" or not access.can_send_message:
        raise PermissionDenied("当前无权发送消息")
    text = str(content or "").strip()
    if not text:
        raise ValidationError({"detail": "消息不能为空"})
    payload = dict(extra_payload or {})
    reply_payload = _build_reply_payload(conversation, quoted_message_id)
    if reply_payload is not None:
        payload["reply_to_message"] = reply_payload
    if access.member is not None and not access.member.show_in_list:
        access.member.show_in_list = True
        access.member.save(update_fields=["show_in_list", "updated_at"])
    message = create_message(conversation, user, text, client_message_id=client_message_id, payload=payload)
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
    if emit_events:
        notify_chat_new_message(user.id, {"conversation_id": conversation.id, "message": message_payload})
        notify_chat_conversation_updated(user.id, sender_conversation)
        for recipient in recipient_payloads:
            notify_chat_new_message(recipient["user_id"], {"conversation_id": conversation.id, "message": message_payload})
            notify_chat_conversation_updated(recipient["user_id"], recipient["conversation"])
            notify_chat_unread_updated(recipient["user_id"], conversation.id, recipient["unread_count"], recipient["total_unread_count"])
    return {
        "detail": "消息已发送",
        "conversation_id": conversation.id,
        "message": message_payload,
        "sender_conversation": sender_conversation,
        "recipients": recipient_payloads,
    }


def execute_mark_conversation_read_command(user, conversation_id: int, *, last_read_sequence: int) -> dict:
    conversation = ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    member = ChatConversationMember.objects.filter(conversation=conversation, user_id=user.id, status=ChatConversationMember.Status.ACTIVE).first()
    if member is None:
        raise PermissionDenied("当前无权操作该会话")
    member = mark_conversation_read(member, last_read_sequence)
    return {
        "conversation_id": conversation.id,
        "unread_count": member.unread_count,
        "total_unread_count": get_total_unread_count(user),
        "last_read_sequence": member.last_read_sequence,
    }