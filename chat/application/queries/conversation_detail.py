from __future__ import annotations

from chat.domain.serialization import serialize_conversation
from chat.infrastructure.repositories import get_active_conversation
from chat.models import ChatConversation


def execute_conversation_detail_query(user, conversation_id: int) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    return serialize_conversation(conversation, user)