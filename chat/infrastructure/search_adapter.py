from __future__ import annotations

from dataclasses import dataclass

from chat.infrastructure.repositories import (
    list_active_conversations_by_ids,
    search_active_users,
    search_discover_group_conversations,
    search_friend_users,
    search_messages_in_conversations,
)


@dataclass(frozen=True)
class ChatSearchMaterials:
    conversations: list
    users: list
    messages: list


def load_chat_search_materials(*, user, keyword: str, scope: str, limit: int, visible_ids: list[int], include_hidden_messages: bool) -> ChatSearchMaterials:
    if scope == "discover":
        return ChatSearchMaterials(
            conversations=list(search_discover_group_conversations(keyword=keyword, limit=limit)),
            users=list(search_active_users(keyword=keyword, limit=limit)),
            messages=[],
        )

    conversations = list(list_active_conversations_by_ids(visible_ids, keyword=keyword, limit=limit))
    if scope == "audit":
        users = list(search_active_users(keyword=keyword, limit=limit))
    else:
        users = search_friend_users(user, keyword=keyword, limit=limit)
    messages = search_messages_in_conversations(
        visible_ids=visible_ids,
        keyword=keyword,
        limit=limit,
        user_id=None if include_hidden_messages else user.id,
        include_hidden=include_hidden_messages,
    )
    return ChatSearchMaterials(
        conversations=conversations,
        users=users,
        messages=messages,
    )