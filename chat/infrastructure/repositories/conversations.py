from __future__ import annotations

from chat.models import ChatConversation


def get_active_conversation(conversation_id: int) -> ChatConversation | None:
    return ChatConversation.objects.select_related("owner", "group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()


def get_active_group_conversation(conversation_id: int) -> ChatConversation | None:
    return ChatConversation.objects.select_related("owner", "group_config").filter(
        id=conversation_id,
        status=ChatConversation.Status.ACTIVE,
        type=ChatConversation.Type.GROUP,
    ).first()


def refresh_conversation(conversation_id: int) -> ChatConversation:
    return ChatConversation.objects.select_related("owner", "group_config").get(pk=conversation_id)


def get_active_direct_conversation_by_pair(pair_key: str) -> ChatConversation | None:
    return ChatConversation.objects.filter(
        direct_pair_key=pair_key,
        status=ChatConversation.Status.ACTIVE,
        type=ChatConversation.Type.DIRECT,
    ).first()


def list_visible_conversations(*, visible_ids: list[int] | set[int], category: str = "all", keyword: str = ""):
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).select_related("owner", "group_config")
    queryset = queryset.filter(id__in=visible_ids)
    if category in {"direct", "group"}:
        queryset = queryset.filter(type=category)
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    return queryset.order_by("-last_message_at", "-id")


def list_active_conversations_by_ids(conversation_ids: list[int] | set[int], *, keyword: str = "", limit: int | None = None):
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE, id__in=conversation_ids).select_related("owner", "group_config")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    queryset = queryset.order_by("-last_message_at", "-id")
    if limit is not None:
        return queryset[:limit]
    return queryset


def list_admin_conversations(*, keyword: str = "", conversation_type: str = "", limit: int = 100):
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).select_related("owner", "group_config")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    if conversation_type in {ChatConversation.Type.DIRECT, ChatConversation.Type.GROUP}:
        queryset = queryset.filter(type=conversation_type)
    return queryset.order_by("-last_message_at")[:limit]