from dataclasses import dataclass

from chat.domain.conversations import create_group_conversation
from chat.domain.serialization import serialize_conversation
from chat.infrastructure.event_bus import notify_chat_conversation_updated


@dataclass(frozen=True)
class CreateGroupConversationCommandResult:
    conversation: dict


def execute_create_group_conversation_command(
    owner,
    *,
    name: str,
    member_users: list,
    join_approval_required: bool,
    allow_member_invite: bool,
) -> CreateGroupConversationCommandResult:
    conversation = create_group_conversation(
        owner,
        name=name,
        member_users=member_users,
        join_approval_required=join_approval_required,
        allow_member_invite=allow_member_invite,
    )
    for user in {owner, *member_users}:
        notify_chat_conversation_updated(user.id, serialize_conversation(conversation, user))
    return CreateGroupConversationCommandResult(
        conversation=serialize_conversation(conversation, owner),
    )