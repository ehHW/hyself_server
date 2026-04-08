from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from chat.domain.access import get_conversation_access
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.friendships import friendship_remark, get_active_friendship_between
from chat.domain.serialization import serialize_friend_request, serialize_friendship
from chat.models import ChatConversation, ChatConversationMember, ChatFriendRequest, ChatFriendship, ChatGroupJoinRequest, build_pair_key


@dataclass(frozen=True)
class ListFriendRequestsQueryParams:
    direction: str = "received"
    status_filter: str = ""


def execute_list_friend_requests_query(user, params: ListFriendRequestsQueryParams) -> dict:
    queryset = ChatFriendRequest.objects.select_related("from_user", "to_user", "handled_by")
    if params.direction == "sent":
        queryset = queryset.filter(from_user=user)
    elif params.direction == "all":
        queryset = queryset.filter(Q(from_user=user) | Q(to_user=user))
    else:
        queryset = queryset.filter(to_user=user)
    if params.status_filter:
        queryset = queryset.filter(status=params.status_filter)
    items = [serialize_friend_request(item) for item in queryset[:100]]
    return {"count": len(items), "next": None, "previous": None, "results": items}


def execute_list_friends_query(user, keyword: str = "") -> dict:
    friendships = ChatFriendship.objects.filter(status=ChatFriendship.Status.ACTIVE).select_related("user_low", "user_high")
    friendships = [item for item in friendships if user.id in {item.user_low_id, item.user_high_id}]
    if keyword:
        lowered = keyword.lower()
        friendships = [
            item
            for item in friendships
            if lowered in (item.user_low.display_name or item.user_low.username).lower() or lowered in (item.user_high.display_name or item.user_high.username).lower()
        ]
    results = [serialize_friendship(item, user) for item in friendships]
    return {"count": len(results), "next": None, "previous": None, "results": results}


def execute_list_group_join_requests_query(user, conversation_id: int | None = None, status_filter: str = "") -> dict:
    queryset = ChatGroupJoinRequest.objects.select_related("conversation", "target_user", "inviter", "reviewer")
    if conversation_id:
        queryset = queryset.filter(conversation_id=conversation_id)
    queryset = queryset.filter(conversation__members__user=user, conversation__members__status=ChatConversationMember.Status.ACTIVE).distinct()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
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
    conversation = ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).first()
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    get_conversation_access(user, conversation)
    items = list(ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE))

    def member_sort_key(item: ChatConversationMember):
        role_order = 0 if item.role == ChatConversationMember.Role.OWNER else 1 if item.role == ChatConversationMember.Role.ADMIN else 2
        nickname = str((item.extra_settings or {}).get("group_nickname", "") or "")
        display = nickname or item.user.display_name or item.user.username
        return (role_order, display.lower())

    result_items = []
    for item in sorted(items, key=member_sort_key):
        friendship = get_active_friendship_between(user.id, item.user_id) or ChatFriendship.objects.filter(pair_key=build_pair_key(user.id, item.user_id)).first()
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