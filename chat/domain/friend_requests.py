from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.domain.conversations import ensure_direct_conversation
from chat.domain.friendships import get_active_friendship_between
from chat.models import ChatConversation, ChatFriendRequest, ChatFriendship, build_pair_key


def create_or_restore_friendship(from_user, to_user, source_request: ChatFriendRequest | None = None) -> ChatFriendship:
    pair_key = build_pair_key(from_user.id, to_user.id)
    low_user, high_user = (from_user, to_user) if from_user.id < to_user.id else (to_user, from_user)
    friendship = ChatFriendship.objects.filter(pair_key=pair_key).first()
    now = timezone.now()
    if friendship is None:
        friendship = ChatFriendship.objects.create(
            pair_key=pair_key,
            user_low=low_user,
            user_high=high_user,
            status=ChatFriendship.Status.ACTIVE,
            source_request=source_request,
            accepted_at=now,
            deleted_at=None,
        )
    else:
        friendship.status = ChatFriendship.Status.ACTIVE
        friendship.source_request = source_request or friendship.source_request
        friendship.accepted_at = now
        friendship.deleted_at = None
        friendship.save(update_fields=["status", "source_request", "accepted_at", "deleted_at", "updated_at"])
    return friendship


def handle_friend_request_action(friend_request: ChatFriendRequest, action: str, actor) -> tuple[ChatFriendRequest, ChatFriendship | None, ChatConversation | None]:
    if friend_request.status != ChatFriendRequest.Status.PENDING:
        raise ValidationError({"detail": "当前申请不可再处理"})
    now = timezone.now()
    friendship = None
    conversation = None
    if action == "accept":
        if friend_request.to_user_id != actor.id:
            raise PermissionDenied("仅接收方可通过好友申请")
        friend_request.status = ChatFriendRequest.Status.ACCEPTED
        friend_request.handled_by = actor
        friend_request.handled_at = now
        friend_request.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
        friendship = create_or_restore_friendship(friend_request.from_user, friend_request.to_user, friend_request)
        conversation = ensure_direct_conversation(friend_request.from_user, friend_request.to_user)
        return friend_request, friendship, conversation
    if action == "reject":
        if friend_request.to_user_id != actor.id:
            raise PermissionDenied("仅接收方可拒绝好友申请")
        friend_request.status = ChatFriendRequest.Status.REJECTED
        friend_request.handled_by = actor
        friend_request.handled_at = now
        friend_request.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
        return friend_request, None, None
    if action == "cancel":
        if friend_request.from_user_id != actor.id:
            raise PermissionDenied("仅发起方可取消好友申请")
        friend_request.status = ChatFriendRequest.Status.CANCELED
        friend_request.handled_by = actor
        friend_request.handled_at = now
        friend_request.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
        return friend_request, None, None
    raise ValidationError({"action": "不支持的操作"})


def create_friend_request(from_user, to_user, request_message: str) -> tuple[str, ChatFriendRequest | None, ChatFriendship | None, ChatConversation | None]:
    if from_user.id == to_user.id:
        raise ValidationError({"detail": "不能给自己发送好友申请"})
    if get_active_friendship_between(from_user.id, to_user.id):
        raise ValidationError({"detail": "你们已经是好友"})

    pair_key = build_pair_key(from_user.id, to_user.id)
    reverse_pending = ChatFriendRequest.objects.filter(from_user=to_user, to_user=from_user, status=ChatFriendRequest.Status.PENDING).first()
    if reverse_pending:
        reverse_pending.status = ChatFriendRequest.Status.ACCEPTED
        reverse_pending.auto_accepted = True
        reverse_pending.handled_by = from_user
        reverse_pending.handled_at = timezone.now()
        reverse_pending.save(update_fields=["status", "auto_accepted", "handled_by", "handled_at", "updated_at"])
        request = ChatFriendRequest.objects.create(
            from_user=from_user,
            to_user=to_user,
            pair_key=pair_key,
            status=ChatFriendRequest.Status.ACCEPTED,
            request_message=request_message,
            auto_accepted=True,
            handled_by=to_user,
            handled_at=timezone.now(),
        )
        friendship = create_or_restore_friendship(from_user, to_user, request)
        conversation = ensure_direct_conversation(from_user, to_user)
        return "auto_accepted", request, friendship, conversation

    if ChatFriendRequest.objects.filter(from_user=from_user, to_user=to_user, status=ChatFriendRequest.Status.PENDING).exists():
        raise ValidationError({"detail": "好友申请已发送，请勿重复提交"})

    request = ChatFriendRequest.objects.create(
        from_user=from_user,
        to_user=to_user,
        pair_key=pair_key,
        status=ChatFriendRequest.Status.PENDING,
        request_message=request_message,
    )
    return "pending", request, None, None