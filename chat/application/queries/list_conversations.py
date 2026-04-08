from dataclasses import dataclass

from chat.models import ChatConversation
from chat.domain.access import get_searchable_conversation_ids
from chat.domain.serialization import serialize_conversation


@dataclass(frozen=True)
class ListConversationsQueryParams:
    category: str = "all"
    keyword: str = ""
    include_hidden: bool = False


def execute_list_conversations_query(user, params: ListConversationsQueryParams) -> dict:
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).select_related("owner", "group_config")
    visible_ids = get_searchable_conversation_ids(user, include_hidden=params.include_hidden)
    queryset = queryset.filter(id__in=visible_ids)
    if params.category in {"direct", "group"}:
        queryset = queryset.filter(type=params.category)
    if params.keyword:
        queryset = queryset.filter(name__icontains=params.keyword)
    results = [serialize_conversation(item, user) for item in queryset.order_by("-last_message_at", "-id")[:200]]
    return {"count": len(results), "next": None, "previous": None, "results": results}