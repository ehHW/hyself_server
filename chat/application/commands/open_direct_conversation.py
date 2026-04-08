from dataclasses import dataclass

from chat.domain.conversations import ensure_direct_conversation
from chat.domain.serialization import serialize_conversation
from ws.events import notify_chat_conversation_updated


@dataclass(frozen=True)
class OpenDirectConversationCommandResult:
    conversation_id: int
    conversation_type: str
    show_in_list: bool


def execute_open_direct_conversation_command(current_user, target_user) -> OpenDirectConversationCommandResult:
    conversation = ensure_direct_conversation(current_user, target_user)
    notify_chat_conversation_updated(current_user.id, serialize_conversation(conversation, current_user))
    notify_chat_conversation_updated(target_user.id, serialize_conversation(conversation, target_user))
    return OpenDirectConversationCommandResult(
        conversation_id=conversation.id,
        conversation_type=conversation.type,
        show_in_list=True,
    )