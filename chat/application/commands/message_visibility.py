from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from chat.domain.access import get_conversation_access
from chat.domain.serialization import serialize_conversation
from chat.models import ChatMessage, ChatMessageVisibility


def execute_delete_message_for_user_command(current_user, message_id: int) -> dict:
    message = ChatMessage.objects.select_related("conversation").filter(id=message_id).first()
    if message is None:
        raise ChatMessage.DoesNotExist()

    access = get_conversation_access(current_user, message.conversation)
    if access.member is None:
        raise PermissionDenied("巡检视角不支持删除消息")

    ChatMessageVisibility.objects.get_or_create(message=message, user=current_user)
    return {
        "detail": "消息已删除",
        "message_id": message.id,
        "conversation": serialize_conversation(message.conversation, current_user),
    }