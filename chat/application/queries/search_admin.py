from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.domain.access import get_searchable_conversation_ids, user_can_review_all_messages
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.preferences import get_or_create_user_preference
from chat.domain.serialization import serialize_conversation, serialize_message
from chat.models import ChatConversation, ChatConversationMember, ChatMessage, build_pair_key


User = get_user_model()


@dataclass(frozen=True)
class ChatSearchQueryParams:
    keyword: str
    limit: int = 5


@dataclass(frozen=True)
class AdminConversationListQueryParams:
    keyword: str = ""
    conversation_type: str = ""


@dataclass(frozen=True)
class AdminMessageListQueryParams:
    conversation_id: int | None = None
    keyword: str = ""


def execute_chat_search_query(user, params: ChatSearchQueryParams) -> dict:
    keyword = str(params.keyword or "").strip()
    if not keyword:
        raise ValidationError({"detail": "搜索关键字不能为空"})
    limit = max(1, min(20, int(params.limit)))
    visible_ids = get_searchable_conversation_ids(user, include_hidden=True)
    conversations = ChatConversation.objects.filter(id__in=visible_ids, status=ChatConversation.Status.ACTIVE, name__icontains=keyword).select_related("owner", "group_config")[:limit]
    users = User.objects.filter(Q(username__icontains=keyword) | Q(display_name__icontains=keyword), deleted_at__isnull=True, is_active=True)[:limit]
    messages = ChatMessage.objects.select_related("sender", "conversation").filter(conversation_id__in=visible_ids, content__icontains=keyword).order_by("-created_at")[:limit]
    conversation_payload_map = {item.id: serialize_conversation(item, user) for item in conversations}
    message_conversation_ids = {item.conversation_id for item in messages}
    if message_conversation_ids:
        for item in ChatConversation.objects.filter(id__in=message_conversation_ids).select_related("owner", "group_config"):
            conversation_payload_map.setdefault(item.id, serialize_conversation(item, user))
    users_payload = []
    for item in users:
        pair_key = build_pair_key(user.id, item.id)
        direct_conversation = ChatConversation.objects.filter(direct_pair_key=pair_key, status=ChatConversation.Status.ACTIVE).first()
        direct_member = None if direct_conversation is None else ChatConversationMember.objects.filter(conversation=direct_conversation, user=user).first()
        users_payload.append(
            {
                "id": item.id,
                "username": item.username,
                "display_name": item.display_name,
                "avatar": item.avatar,
                "can_open_direct": item.id != user.id,
                "direct_conversation": None if direct_conversation is None else {"id": direct_conversation.id, "show_in_list": True if direct_member is None else direct_member.show_in_list},
            }
        )
    return {
        "keyword": keyword,
        "conversations": [{"id": item.id, "type": item.type, "name": conversation_payload_map[item.id]["name"], "access_mode": conversation_payload_map[item.id]["access_mode"]} for item in conversations],
        "users": users_payload,
        "messages": [{"conversation_id": item.conversation_id, "conversation_name": conversation_payload_map[item.conversation_id]["name"], "message_id": item.id, "sequence": item.sequence, "message_type": item.message_type, "content_preview": item.content[:80], "sender": None if item.sender is None else user_brief(item.sender), "created_at": to_serializable_datetime(item.created_at)} for item in messages],
    }


def execute_get_chat_settings_query(user) -> dict:
    preference = get_or_create_user_preference(user)
    return {
        "theme_mode": "dark" if preference.theme_mode == "dark" else "light",
        "chat_receive_notification": bool(preference.chat_receive_notification),
        "chat_list_sort_mode": preference.chat_list_sort_mode,
        "chat_stealth_inspect_enabled": bool(preference.chat_stealth_inspect_enabled),
        "settings_json": preference.settings_json or {},
    }


def execute_admin_conversation_list_query(user, params: AdminConversationListQueryParams) -> dict:
    if not user_can_review_all_messages(user):
        raise PermissionDenied("当前无权查看全部会话")
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).select_related("owner", "group_config")
    if params.keyword:
        queryset = queryset.filter(name__icontains=params.keyword)
    if params.conversation_type in {ChatConversation.Type.DIRECT, ChatConversation.Type.GROUP}:
        queryset = queryset.filter(type=params.conversation_type)
    results = [serialize_conversation(item, user) for item in queryset.order_by("-last_message_at")[:100]]
    return {"count": len(results), "next": None, "previous": None, "results": results}


def execute_admin_message_list_query(user, params: AdminMessageListQueryParams) -> dict:
    if not user_can_review_all_messages(user):
        raise PermissionDenied("当前无权查看全部聊天记录")
    queryset = ChatMessage.objects.select_related("sender", "conversation")
    if params.conversation_id:
        queryset = queryset.filter(conversation_id=params.conversation_id)
    if params.keyword:
        queryset = queryset.filter(content__icontains=params.keyword)
    items = [serialize_message(item) | {"conversation_id": item.conversation_id} for item in queryset.order_by("-created_at")[:200]]
    return {"count": len(items), "next": None, "previous": None, "results": items}