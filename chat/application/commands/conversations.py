from __future__ import annotations

from chat.domain.access import get_conversation_denied_detail, get_member
from chat.domain.member_settings import get_member_preferences, update_member_preferences
from chat.domain.serialization import serialize_conversation
from chat.infrastructure.repositories import get_active_conversation
from chat.models import ChatConversation
from chat.infrastructure.event_bus import notify_chat_conversation_updated


def execute_hide_conversation_command(current_user, conversation_id: int) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    member = get_member(conversation, current_user.id, active_only=True)
    if member is None:
        raise PermissionError(get_conversation_denied_detail(conversation, current_user.id, action="隐藏该会话"))
    member.show_in_list = False
    member.save(update_fields=["show_in_list", "updated_at"])
    notify_chat_conversation_updated(current_user.id, serialize_conversation(conversation, current_user))
    return {"detail": "会话已从列表移除", "conversation_id": conversation.id, "show_in_list": False}


def execute_update_conversation_preference_command(current_user, conversation_id: int, *, mute_notifications: bool | None = None, group_nickname: str | None = None) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    member = get_member(conversation, current_user.id, active_only=True)
    if member is None:
        raise PermissionError(get_conversation_denied_detail(conversation, current_user.id, action="更新会话设置"))
    update_member_preferences(member, mute_notifications=mute_notifications, group_nickname=group_nickname)
    payload = serialize_conversation(conversation, current_user)
    notify_chat_conversation_updated(current_user.id, payload)
    return {"detail": "会话设置已更新", "conversation": payload, "member_settings": get_member_preferences(member)}


def execute_toggle_conversation_pin_command(current_user, conversation_id: int, *, is_pinned: bool) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    member = get_member(conversation, current_user.id, active_only=True)
    if member is None:
        raise PermissionError(get_conversation_denied_detail(conversation, current_user.id, action="置顶该会话"))
    member.is_pinned = is_pinned
    member.save(update_fields=["is_pinned", "updated_at"])
    payload = serialize_conversation(conversation, current_user)
    notify_chat_conversation_updated(current_user.id, payload)
    return {"detail": "会话置顶状态已更新", "conversation": payload}