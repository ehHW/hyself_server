from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.domain.common import user_brief
from chat.domain.friend_requests import create_friend_request, handle_friend_request_action
from chat.domain.friendships import get_active_friendship_between, update_friendship_remark
from chat.domain.serialization import serialize_conversation, serialize_friend_request, serialize_friendship
from chat.models import ChatConversation, ChatFriendRequest, ChatFriendship, build_pair_key
from ws.events import notify_chat_conversation_updated, notify_chat_friend_request_updated, notify_chat_friendship_updated


User = get_user_model()


@dataclass(frozen=True)
class SubmitFriendRequestCommandResult:
    payload: dict
    status_code: int = 200


def execute_submit_friend_request_command(current_user, target_user_id: int, request_message: str) -> SubmitFriendRequestCommandResult:
    target_user = User.objects.filter(id=target_user_id, deleted_at__isnull=True, is_active=True).first()
    if target_user is None:
        raise User.DoesNotExist()
    mode, friend_request, friendship, conversation = create_friend_request(current_user, target_user, request_message)
    if friend_request:
        notify_chat_friend_request_updated(target_user.id, serialize_friend_request(friend_request))
    if mode == "auto_accepted" and friendship and conversation:
        for actor in [current_user, target_user]:
            other_user = target_user if actor.id == current_user.id else current_user
            notify_chat_friendship_updated(
                actor.id,
                {"action": "accepted", "friend_user": user_brief(other_user), "conversation": {"id": conversation.id, "type": conversation.type, "show_in_list": True}},
            )
            notify_chat_conversation_updated(actor.id, serialize_conversation(conversation, actor))
        return SubmitFriendRequestCommandResult(
            payload={
                "mode": mode,
                "detail": "双方已自动成为好友",
                "friendship": serialize_friendship(friendship, current_user),
                "conversation": {"id": conversation.id, "type": conversation.type, "show_in_list": True},
            }
        )
    return SubmitFriendRequestCommandResult(payload={"mode": mode, "detail": "好友申请已发送", "request": serialize_friend_request(friend_request)})


def execute_handle_friend_request_command(current_user, request_id: int, action: str) -> dict:
    friend_request = ChatFriendRequest.objects.select_related("from_user", "to_user", "handled_by").filter(id=request_id).first()
    if friend_request is None:
        raise ChatFriendRequest.DoesNotExist()
    friend_request, friendship, conversation = handle_friend_request_action(friend_request, action, current_user)
    for current_user_id in {friend_request.from_user_id, friend_request.to_user_id}:
        notify_chat_friend_request_updated(current_user_id, serialize_friend_request(friend_request))
    response = {"detail": "好友申请已处理", "request": {"id": friend_request.id, "status": friend_request.status}}
    if friendship and conversation:
        response["friendship"] = {"id": friendship.id, "status": friendship.status}
        response["conversation"] = {"id": conversation.id, "type": conversation.type}
        notify_chat_friendship_updated(friend_request.from_user_id, {"action": "accepted", "friend_user": user_brief(friend_request.to_user), "conversation": {"id": conversation.id, "type": conversation.type, "show_in_list": True}})
        notify_chat_friendship_updated(friend_request.to_user_id, {"action": "accepted", "friend_user": user_brief(friend_request.from_user), "conversation": {"id": conversation.id, "type": conversation.type, "show_in_list": True}})
        notify_chat_conversation_updated(friend_request.from_user_id, serialize_conversation(conversation, friend_request.from_user))
        notify_chat_conversation_updated(friend_request.to_user_id, serialize_conversation(conversation, friend_request.to_user))
    return response


def execute_delete_friend_command(current_user, friend_user_id: int) -> dict:
    friendship = get_active_friendship_between(current_user.id, friend_user_id)
    if friendship is None:
        raise ChatFriendship.DoesNotExist()
    friendship.status = ChatFriendship.Status.DELETED
    friendship.deleted_at = timezone.now()
    friendship.save(update_fields=["status", "deleted_at", "updated_at"])
    notify_chat_friendship_updated(current_user.id, {"action": "deleted", "friend_user": {"id": friend_user_id}})
    notify_chat_friendship_updated(friend_user_id, {"action": "deleted", "friend_user": {"id": current_user.id}})
    return {"detail": "已删除好友", "friend_user_id": friend_user_id}


def execute_update_friend_setting_command(current_user, friend_user_id: int, *, remark: str | None = None) -> dict:
    friendship = ChatFriendship.objects.filter(pair_key=build_pair_key(current_user.id, friend_user_id)).first()
    if friendship is None:
        raise ChatFriendship.DoesNotExist()
    if remark is not None:
        update_friendship_remark(friendship, current_user.id, remark)
    conversation = ChatConversation.objects.filter(direct_pair_key=friendship.pair_key, status=ChatConversation.Status.ACTIVE).first()
    if conversation is not None:
        target_user = User.objects.filter(id=friend_user_id, deleted_at__isnull=True, is_active=True).first()
        for actor in [current_user, target_user]:
            if actor is not None:
                notify_chat_conversation_updated(actor.id, serialize_conversation(conversation, actor))
    return {"detail": "好友设置已更新", "remark": friendship.remark_low if friendship.user_low_id == current_user.id else friendship.remark_high}