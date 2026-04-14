from __future__ import annotations

from dataclasses import dataclass

from rest_framework.exceptions import PermissionDenied

from chat.domain.access import get_conversation_denied_detail, get_conversation_access, get_member
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.friendships import friendship_remark, get_active_friendship_between
from chat.domain.serialization import serialize_friend_request, serialize_friendship
from chat.infrastructure.repositories import get_accessible_group_members, get_friendship_by_pair, list_friend_requests_for_user, list_friendships_for_user, list_group_join_requests_for_user
from chat.models import ChatConversation, build_pair_key


@dataclass(frozen=True)
class ListFriendRequestsQueryParams:
    direction: str = "received"
    status_filter: str = ""


def execute_list_friend_requests_query(user, params: ListFriendRequestsQueryParams) -> dict:
    queryset = list_friend_requests_for_user(user, direction=params.direction, status_filter=params.status_filter)
    items = [serialize_friend_request(item) for item in queryset[:100]]
    return {"count": len(items), "next": None, "previous": None, "results": items}


def execute_list_friends_query(user, keyword: str = "") -> dict:
    friendships = list_friendships_for_user(user, keyword=keyword)
    results = [serialize_friendship(item, user) for item in friendships]
    return {"count": len(results), "next": None, "previous": None, "results": results}


def execute_list_group_join_requests_query(user, conversation_id: int | None = None, status_filter: str = "") -> dict:
    queryset = list_group_join_requests_for_user(user, conversation_id=conversation_id, status_filter=status_filter)
    items = [
        {
            "id": item.id,
            "conversation_id": item.conversation_id,
            "status": item.status,
            "target_user": user_brief(item.target_user),
            "created_at": to_serializable_datetime(item.created_at),
        }
        for item in queryset[:100]
    ]
    return {"count": len(items), "next": None, "previous": None, "results": items}


def execute_list_group_members_query(user, conversation_id: int) -> dict:
    conversation, items = get_accessible_group_members(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    if get_member(conversation, user.id, active_only=True) is None:
        raise PermissionDenied(get_conversation_denied_detail(conversation, user.id, action="查看群成员"))

    def member_sort_key(item):
        role_order = 0 if item.role == conversation.members.model.Role.OWNER else 1 if item.role == conversation.members.model.Role.ADMIN else 2
        nickname = str((item.extra_settings or {}).get("group_nickname", "") or "")
        display = nickname or item.user.display_name or item.user.username
        return (role_order, display.lower())

    result_items = []
    for item in sorted(items, key=member_sort_key):
        friendship = get_active_friendship_between(user.id, item.user_id) or get_friendship_by_pair(build_pair_key(user.id, item.user_id))
        result_items.append(
            {
                "user": user_brief(item.user),
                "role": item.role,
                "status": item.status,
                "mute_until": to_serializable_datetime(item.mute_until),
                "joined_at": to_serializable_datetime(item.joined_at),
                "group_nickname": str((item.extra_settings or {}).get("group_nickname", "") or ""),
                "friend_remark": friendship_remark(friendship, user.id) or None,
            }
        )
    return {"conversation_id": conversation.id, "items": result_items}