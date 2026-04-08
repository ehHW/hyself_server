from __future__ import annotations

from chat.models import ChatFriendship, build_pair_key


def get_active_friendship_between(user_a_id: int, user_b_id: int) -> ChatFriendship | None:
    pair_key = build_pair_key(user_a_id, user_b_id)
    return ChatFriendship.objects.filter(pair_key=pair_key, status=ChatFriendship.Status.ACTIVE).first()


def friendship_counterparty(friendship: ChatFriendship, current_user_id: int):
    return friendship.user_high if friendship.user_low_id == current_user_id else friendship.user_low


def friendship_remark(friendship: ChatFriendship | None, current_user_id: int) -> str:
    if friendship is None:
        return ""
    return friendship.remark_low if friendship.user_low_id == current_user_id else friendship.remark_high


def update_friendship_remark(friendship: ChatFriendship, current_user_id: int, remark: str) -> ChatFriendship:
    if friendship.user_low_id == current_user_id:
        friendship.remark_low = remark
        friendship.save(update_fields=["remark_low", "updated_at"])
        return friendship
    friendship.remark_high = remark
    friendship.save(update_fields=["remark_high", "updated_at"])
    return friendship