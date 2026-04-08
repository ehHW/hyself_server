from __future__ import annotations

from chat.domain.access import get_conversation_access
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.friendships import friendship_counterparty, friendship_remark, get_active_friendship_between
from chat.domain.member_settings import get_member_preferences
from chat.models import ChatConversation, ChatConversationMember, ChatFriendRequest, ChatFriendship, ChatGroupConfig, ChatMessage, build_pair_key


def serialize_message(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "sequence": message.sequence,
        "client_message_id": message.client_message_id,
        "message_type": message.message_type,
        "content": message.content,
        "payload": message.payload or {},
        "is_system": message.is_system,
        "sender": None if message.sender is None else user_brief(message.sender),
        "created_at": to_serializable_datetime(message.created_at),
    }


def serialize_group_config(group_config: ChatGroupConfig | None) -> dict | None:
    if group_config is None:
        return None
    return {
        "join_approval_required": group_config.join_approval_required,
        "allow_member_invite": group_config.allow_member_invite,
        "max_members": group_config.max_members,
        "mute_all": group_config.mute_all,
    }


def serialize_conversation(conversation: ChatConversation, user) -> dict:
    access = get_conversation_access(user, conversation)
    member = access.member
    display_name = conversation.name
    avatar = conversation.avatar
    direct_target = None
    friend_remark = None
    if conversation.type == ChatConversation.Type.DIRECT and member:
        other_member = ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).exclude(user_id=user.id).first()
        if other_member:
            display_name = other_member.user.display_name or other_member.user.username
            avatar = other_member.user.avatar
            direct_target = user_brief(other_member.user)
            friendship = get_active_friendship_between(user.id, other_member.user_id) or ChatFriendship.objects.filter(pair_key=build_pair_key(user.id, other_member.user_id)).first()
            friend_remark = friendship_remark(friendship, user.id) or None
    return {
        "id": conversation.id,
        "type": conversation.type,
        "name": display_name,
        "avatar": avatar,
        "direct_target": direct_target,
        "friend_remark": friend_remark,
        "is_pinned": False if member is None else member.is_pinned,
        "access_mode": access.access_mode,
        "member_role": None if member is None else member.role,
        "show_in_list": True if member is None else member.show_in_list,
        "unread_count": 0 if member is None else member.unread_count,
        "last_message_preview": conversation.last_message_preview,
        "last_message_at": to_serializable_datetime(conversation.last_message_at),
        "member_count": conversation.member_count_cache,
        "can_send_message": access.can_send_message,
        "status": conversation.status,
        "last_read_sequence": 0 if member is None else member.last_read_sequence,
        "member_settings": get_member_preferences(member),
        "group_config": serialize_group_config(getattr(conversation, "group_config", None)) if conversation.type == ChatConversation.Type.GROUP else None,
        "owner": None if conversation.owner is None else user_brief(conversation.owner),
    }


def serialize_friend_request(friend_request: ChatFriendRequest) -> dict:
    return {
        "id": friend_request.id,
        "status": friend_request.status,
        "from_user": user_brief(friend_request.from_user),
        "to_user": user_brief(friend_request.to_user),
        "request_message": friend_request.request_message,
        "auto_accepted": friend_request.auto_accepted,
        "handled_by": None if friend_request.handled_by is None else user_brief(friend_request.handled_by),
        "handled_at": to_serializable_datetime(friend_request.handled_at),
        "created_at": to_serializable_datetime(friend_request.created_at),
    }


def serialize_friendship(friendship: ChatFriendship, current_user) -> dict:
    friend_user = friendship_counterparty(friendship, current_user.id)
    direct_conversation = ChatConversation.objects.filter(direct_pair_key=friendship.pair_key).first()
    direct_member = None if direct_conversation is None else ChatConversationMember.objects.filter(conversation=direct_conversation, user=current_user).first()
    return {
        "friendship_id": friendship.id,
        "friend_user": user_brief(friend_user),
        "accepted_at": to_serializable_datetime(friendship.accepted_at),
        "remark": friendship_remark(friendship, current_user.id),
        "direct_conversation": None if direct_conversation is None else {"id": direct_conversation.id, "show_in_list": True if direct_member is None else direct_member.show_in_list},
    }