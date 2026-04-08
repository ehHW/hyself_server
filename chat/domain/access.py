from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from chat.domain.preferences import get_or_create_user_preference
from chat.models import ChatConversation, ChatConversationMember


@dataclass
class ConversationAccess:
    conversation: ChatConversation
    member: ChatConversationMember | None
    access_mode: str
    can_send_message: bool


def get_member(conversation: ChatConversation, user_id: int, active_only: bool = False) -> ChatConversationMember | None:
    queryset = ChatConversationMember.objects.filter(conversation=conversation, user_id=user_id)
    if active_only:
        queryset = queryset.filter(status=ChatConversationMember.Status.ACTIVE)
    return queryset.first()


def user_can_stealth_inspect(user) -> bool:
    if not user.is_authenticated or not user.is_superuser:
        return False
    preference = get_or_create_user_preference(user)
    return bool(preference.chat_stealth_inspect_enabled)


def user_can_review_all_messages(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.has_permission_code("chat.review_all_messages")))


def get_conversation_access(user, conversation: ChatConversation) -> ConversationAccess:
    member = get_member(conversation, user.id, active_only=True)
    if member:
        mute_active = bool(member.mute_until and member.mute_until > timezone.now())
        can_send = conversation.status == ChatConversation.Status.ACTIVE and not mute_active
        if conversation.type == ChatConversation.Type.GROUP and hasattr(conversation, "group_config") and conversation.group_config.mute_all and member.role != ChatConversationMember.Role.OWNER:
            can_send = False
        return ConversationAccess(conversation=conversation, member=member, access_mode="member", can_send_message=can_send)

    if user_can_stealth_inspect(user):
        return ConversationAccess(conversation=conversation, member=None, access_mode="stealth_readonly", can_send_message=False)

    raise PermissionDenied("当前无权访问该会话")


def get_visible_conversations_queryset(user):
    member_ids = ChatConversationMember.objects.filter(user=user, status=ChatConversationMember.Status.ACTIVE, show_in_list=True).values_list("conversation_id", flat=True)
    queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE, id__in=member_ids).select_related("owner", "group_config")
    if user_can_stealth_inspect(user):
        queryset = ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).select_related("owner", "group_config")
    return queryset.distinct().order_by("-last_message_at", "-id")


def get_searchable_conversation_ids(user, include_hidden: bool = False) -> list[int]:
    if user_can_stealth_inspect(user):
        return list(ChatConversation.objects.filter(status=ChatConversation.Status.ACTIVE).values_list("id", flat=True))
    queryset = ChatConversationMember.objects.filter(user=user, status=ChatConversationMember.Status.ACTIVE)
    if not include_hidden:
        queryset = queryset.filter(show_in_list=True)
    return list(queryset.values_list("conversation_id", flat=True))