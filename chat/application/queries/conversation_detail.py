from __future__ import annotations

from chat.domain.serialization import serialize_conversation
from chat.models import ChatConversation


def execute_conversation_detail_query(user, conversation_id: int) -> dict:
    conversation = ChatConversation.objects.select_related("owner", "group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    return serialize_conversation(conversation, user)