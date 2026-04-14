from __future__ import annotations

from chat.domain.access import get_conversation_denied_detail, get_member
from chat.domain.common import user_brief
from chat.infrastructure.repositories import get_active_conversation, list_other_active_member_user_ids
from chat.models import ChatConversation
from rest_framework.exceptions import PermissionDenied, ValidationError


def execute_chat_typing_query(user, conversation_id: int, *, is_typing: bool) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})
    member = get_member(conversation, user.id, active_only=True)
    if member is None:
        raise PermissionDenied(get_conversation_denied_detail(conversation, user.id, action="发送输入状态"))
    target_user_ids = list_other_active_member_user_ids(conversation, exclude_user_id=user.id)
    return {
        "conversation_id": conversation.id,
        "user": user_brief(user),
        "is_typing": bool(is_typing),
        "target_user_ids": target_user_ids,
    }