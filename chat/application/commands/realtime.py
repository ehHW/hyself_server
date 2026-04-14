from __future__ import annotations

from chat.auth.permissions import ensure_chat_permission
from chat.application.commands.delivery import build_message_delivery_payloads, emit_message_delivery_events
from chat.application.commands.message_payloads import build_reply_payload_from_message
from chat.domain.access import get_conversation_access, get_conversation_denied_detail
from chat.domain.messaging import create_message, get_total_unread_count, mark_conversation_read
from chat.infrastructure.repositories import get_active_conversation, get_active_member, get_conversation_message
from chat.models import ChatConversation, ChatMessage
from rest_framework.exceptions import PermissionDenied, ValidationError


def _build_reply_payload(conversation: ChatConversation, quoted_message_id: int | None) -> dict | None:
    if not quoted_message_id:
        return None
    quoted_message = get_conversation_message(conversation, quoted_message_id)
    if quoted_message is None:
        raise ValidationError({"quoted_message_id": "引用消息不存在"})
    return build_reply_payload_from_message(quoted_message)


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
    ensure_chat_permission(user, "chat.send_message", "当前角色无发送消息权限")
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    access = get_conversation_access(user, conversation)
    if access.access_mode == "former_member_readonly":
        raise PermissionDenied(get_conversation_denied_detail(conversation, user.id, action="发送新消息"))
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
    conversation, message_payload, sender_conversation, recipient_payloads = build_message_delivery_payloads(
        conversation=conversation,
        sender_user=user,
        message=message,
    )
    if emit_events:
        emit_message_delivery_events(
            conversation=conversation,
            sender_user=user,
            message_payload=message_payload,
            sender_conversation=sender_conversation,
            recipient_payloads=recipient_payloads,
        )
    return {
        "detail": "消息已发送",
        "conversation_id": conversation.id,
        "message": message_payload,
        "sender_conversation": sender_conversation,
        "recipients": recipient_payloads,
    }


def execute_mark_conversation_read_command(user, conversation_id: int, *, last_read_sequence: int) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    member = get_active_member(conversation, user.id)
    if member is None:
        raise PermissionDenied(get_conversation_denied_detail(conversation, user.id, action="标记会话已读"))
    member = mark_conversation_read(member, last_read_sequence)
    return {
        "conversation_id": conversation.id,
        "unread_count": member.unread_count,
        "total_unread_count": get_total_unread_count(user),
        "last_read_sequence": member.last_read_sequence,
    }