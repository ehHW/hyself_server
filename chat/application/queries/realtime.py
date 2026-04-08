from __future__ import annotations

from chat.domain.access import get_member
from chat.domain.common import user_brief
from chat.models import ChatConversation, ChatConversationMember
from rest_framework.exceptions import PermissionDenied, ValidationError


def execute_chat_typing_query(user, conversation_id: int, *, is_typing: bool) -> dict:
    conversation = ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    member = get_member(conversation, user.id, active_only=True)
    if member is None:
        raise PermissionDenied("当前无权操作该会话")
    target_user_ids = list(
        ChatConversationMember.objects.filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).exclude(user_id=user.id).values_list("user_id", flat=True)
    )
    return {
        "conversation_id": conversation.id,
        "user": user_brief(user),
        "is_typing": bool(is_typing),
        "target_user_ids": target_user_ids,
    }