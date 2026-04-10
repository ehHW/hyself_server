from dataclasses import dataclass

from chat.domain.access import get_searchable_conversation_ids
from chat.domain.serialization import serialize_conversation
from chat.infrastructure.repositories import list_visible_conversations


@dataclass(frozen=True)
class ListConversationsQueryParams:
    category: str = "all"
    keyword: str = ""
    include_hidden: bool = False


def execute_list_conversations_query(user, params: ListConversationsQueryParams) -> dict:
    visible_ids = get_searchable_conversation_ids(user, include_hidden=params.include_hidden)
    queryset = list_visible_conversations(visible_ids=visible_ids, category=params.category, keyword=params.keyword)
    results = [serialize_conversation(item, user) for item in queryset.order_by("-last_message_at", "-id")[:200]]
    return {"count": len(results), "next": None, "previous": None, "results": results}