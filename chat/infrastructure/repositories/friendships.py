from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q

from chat.models import ChatFriendRequest, ChatFriendship


User = get_user_model()


def search_active_users(*, keyword: str, limit: int):
    return User.objects.filter(
        Q(username__icontains=keyword) | Q(display_name__icontains=keyword),
        deleted_at__isnull=True,
        is_active=True,
    )[:limit]


def list_friend_requests_for_user(user, *, direction: str, status_filter: str = ""):
    queryset = ChatFriendRequest.objects.select_related("from_user", "to_user", "handled_by")
    if direction == "sent":
        queryset = queryset.filter(from_user=user)
    elif direction == "all":
        queryset = queryset.filter(Q(from_user=user) | Q(to_user=user))
    else:
        queryset = queryset.filter(to_user=user)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    return queryset[:100]


def get_friend_request_with_users(request_id: int) -> ChatFriendRequest | None:
    return ChatFriendRequest.objects.select_related("from_user", "to_user", "handled_by").filter(id=request_id).first()


def get_friendship_by_pair(pair_key: str) -> ChatFriendship | None:
    return ChatFriendship.objects.select_related("user_low", "user_high").filter(pair_key=pair_key).first()


def get_active_user(user_id: int):
    return User.objects.filter(id=user_id, deleted_at__isnull=True, is_active=True).first()


def list_friendships_for_user(user, *, keyword: str = "") -> list[ChatFriendship]:
    friendships = ChatFriendship.objects.filter(status=ChatFriendship.Status.ACTIVE).filter(Q(user_low=user) | Q(user_high=user)).select_related("user_low", "user_high")
    results = list(friendships)
    if keyword:
        lowered = keyword.lower()
        results = [
            item
            for item in results
            if lowered in (item.user_low.display_name or item.user_low.username).lower() or lowered in (item.user_high.display_name or item.user_high.username).lower()
        ]
    return results


def search_friend_users(user, *, keyword: str, limit: int):
    friend_links = list(
        ChatFriendship.objects.select_related("user_low", "user_high")
        .filter(status=ChatFriendship.Status.ACTIVE)
        .filter(Q(user_low_id=user.id) | Q(user_high_id=user.id))
    )
    users = []
    keyword_lower = keyword.lower()
    for friendship in friend_links:
        target_user = friendship.user_high if friendship.user_low_id == user.id else friendship.user_low
        haystack = f"{target_user.username} {target_user.display_name}".lower()
        if keyword_lower not in haystack:
            continue
        users.append(target_user)
        if len(users) >= limit:
            break
    return users