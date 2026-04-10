from __future__ import annotations

from chat.models import ChatConversation, ChatMessage, ChatMessageVisibility


def get_conversation_message(conversation: ChatConversation, message_id: int) -> ChatMessage | None:
    return ChatMessage.objects.select_related("sender").filter(conversation=conversation, id=message_id).first()


def _exclude_hidden_for_user(queryset, user_id: int | None = None):
    if not user_id:
        return queryset
    hidden_message_ids = ChatMessageVisibility.objects.filter(user_id=user_id).values_list("message_id", flat=True)
    return queryset.exclude(id__in=hidden_message_ids)


def list_conversation_messages(conversation: ChatConversation, *, user_id: int | None = None, include_hidden: bool = False):
    queryset = ChatMessage.objects.select_related("sender", "conversation").filter(conversation=conversation)
    if include_hidden:
        return queryset
    return _exclude_hidden_for_user(queryset, user_id)


def search_messages_in_conversations(*, visible_ids: list[int] | set[int], keyword: str, limit: int, user_id: int | None = None, include_hidden: bool = False):
    queryset = ChatMessage.objects.select_related("sender", "conversation").filter(
        conversation_id__in=visible_ids,
        content__icontains=keyword,
    )
    if not include_hidden:
        queryset = _exclude_hidden_for_user(queryset, user_id)
    return queryset.order_by("-created_at")[:limit]


def list_admin_messages(*, conversation_id: int | None = None, keyword: str = "", limit: int = 200):
    queryset = ChatMessage.objects.select_related("sender", "conversation")
    if conversation_id:
        queryset = queryset.filter(conversation_id=conversation_id)
    if keyword:
        queryset = queryset.filter(content__icontains=keyword)
    return queryset.order_by("-created_at")[:limit]


def get_latest_visible_message(conversation: ChatConversation, *, user_id: int | None = None, include_hidden: bool = False) -> ChatMessage | None:
    queryset = ChatMessage.objects.select_related("sender", "conversation").filter(conversation=conversation)
    if not include_hidden:
        queryset = _exclude_hidden_for_user(queryset, user_id)
    return queryset.order_by("-sequence", "-id").first()


def list_forwardable_messages(message_ids: list[int]) -> list[ChatMessage]:
    return list(
        ChatMessage.objects.select_related("conversation", "sender")
        .filter(id__in=message_ids, is_system=False)
        .order_by("created_at", "sequence", "id")
    )