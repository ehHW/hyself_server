from __future__ import annotations

from dataclasses import dataclass

from chat.domain.access import get_conversation_access
from chat.domain.serialization import serialize_message
from chat.models import ChatConversation, ChatMessage


@dataclass(frozen=True)
class ConversationMessagesQueryParams:
    before_sequence: int | None = None
    after_sequence: int | None = None
    around_sequence: int | None = None
    limit: int = 30


def execute_conversation_messages_query(user, conversation_id: int, params: ConversationMessagesQueryParams) -> dict:
    conversation = ChatConversation.objects.select_related("owner", "group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    access = get_conversation_access(user, conversation)
    if access.member is not None and not access.member.show_in_list:
        access.member.show_in_list = True
        access.member.save(update_fields=["show_in_list", "updated_at"])
    queryset = ChatMessage.objects.select_related("sender").filter(conversation=conversation)
    if params.around_sequence:
        anchor_sequence = int(params.around_sequence)
        around_queryset = queryset.filter(sequence__lte=anchor_sequence).order_by("-sequence")
        messages = list(reversed(list(around_queryset[: params.limit])))
        has_more_before = around_queryset.count() > params.limit
        has_more_after = queryset.filter(sequence__gt=anchor_sequence).exists()
    elif params.before_sequence:
        before_queryset = queryset.filter(sequence__lt=int(params.before_sequence)).order_by("-sequence")
        messages = list(reversed(list(before_queryset[: params.limit])))
        has_more_before = before_queryset.count() > params.limit
        has_more_after = False
    elif params.after_sequence:
        after_queryset = queryset.filter(sequence__gt=int(params.after_sequence)).order_by("sequence")
        messages = list(after_queryset[: params.limit])
        has_more_before = False
        has_more_after = after_queryset.count() > params.limit
    else:
        latest_queryset = queryset.order_by("-sequence")
        messages = list(reversed(list(latest_queryset[: params.limit])))
        has_more_before = queryset.count() > params.limit
        has_more_after = False
    first_sequence = messages[0].sequence if messages else None
    last_sequence = messages[-1].sequence if messages else None
    return {
        "conversation": {
            "id": conversation.id,
            "type": conversation.type,
            "access_mode": access.access_mode,
            "can_send_message": access.can_send_message,
        },
        "cursor": {
            "before_sequence": first_sequence,
            "after_sequence": last_sequence,
            "has_more_before": has_more_before,
            "has_more_after": has_more_after,
        },
        "items": [serialize_message(item) for item in messages],
    }