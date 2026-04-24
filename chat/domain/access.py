from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from chat.domain.friendships import get_friendship_between
from chat.domain.preferences import get_or_create_user_preference
from chat.models import ChatConversation, ChatConversationMember, ChatFriendship


@dataclass
class ConversationCapabilities:
    can_view: bool
    can_open: bool
    can_read_history: bool
    can_send_message: bool
    can_mark_read: bool
    can_view_members: bool
    can_manage_members: bool
    can_manage_group_settings: bool
    can_invite_members: bool
    can_join: bool


@dataclass
class ConversationAccess:
    conversation: ChatConversation
    member: ChatConversationMember | None
    access_mode: str
    can_send_message: bool
    capabilities: ConversationCapabilities


def serialize_conversation_capabilities(capabilities: ConversationCapabilities) -> dict:
    return {
        "can_view": capabilities.can_view,
        "can_open": capabilities.can_open,
        "can_read_history": capabilities.can_read_history,
        "can_send_message": capabilities.can_send_message,
        "can_mark_read": capabilities.can_mark_read,
        "can_view_members": capabilities.can_view_members,
        "can_manage_members": capabilities.can_manage_members,
        "can_manage_group_settings": capabilities.can_manage_group_settings,
        "can_invite_members": capabilities.can_invite_members,
        "can_join": capabilities.can_join,
    }


def build_conversation_capabilities(conversation: ChatConversation, member: ChatConversationMember | None, *, access_mode: str, can_send_message: bool) -> ConversationCapabilities:
    is_member_access = access_mode == "member" and member is not None
    is_group = conversation.type == ChatConversation.Type.GROUP
    can_manage_members = bool(
        is_group
        and is_member_access
        and member.role in {ChatConversationMember.Role.OWNER, ChatConversationMember.Role.ADMIN}
    )
    can_manage_group_settings = bool(
        is_group
        and is_member_access
        and member.role == ChatConversationMember.Role.OWNER
    )
    allow_member_invite = bool(
        is_group
        and is_member_access
        and hasattr(conversation, "group_config")
        and conversation.group_config.allow_member_invite
    )
    can_open = access_mode != "discover_preview"
    can_read_history = access_mode != "discover_preview"
    return ConversationCapabilities(
        can_view=True,
        can_open=can_open,
        can_read_history=can_read_history,
        can_send_message=can_send_message,
        can_mark_read=is_member_access,
        can_view_members=is_member_access,
        can_manage_members=can_manage_members,
        can_manage_group_settings=can_manage_group_settings,
        can_invite_members=can_manage_members or allow_member_invite,
        can_join=access_mode == "discover_preview",
    )


def build_discover_preview_capabilities() -> ConversationCapabilities:
    return build_conversation_capabilities(
        ChatConversation(type=ChatConversation.Type.GROUP),
        None,
        access_mode="discover_preview",
        can_send_message=False,
    )


def get_conversation_denied_detail(conversation: ChatConversation, user_id: int, *, action: str = "访问该会话") -> str:
    if conversation.type == ChatConversation.Type.GROUP:
        member = get_member(conversation, user_id, active_only=False)
        base = "你已退出该群聊" if member and member.status == ChatConversationMember.Status.LEFT else "你不是该群成员"
        return base if not action else f"{base}，无法{action}"
    return "当前无权访问该会话" if action == "访问该会话" else f"当前无权{action}"


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
        if conversation.type == ChatConversation.Type.DIRECT:
            peer_user_id = (
                ChatConversationMember.objects.filter(
                    conversation=conversation,
                    status=ChatConversationMember.Status.ACTIVE,
                )
                .exclude(user_id=user.id)
                .values_list("user_id", flat=True)
                .first()
            )
            if peer_user_id is not None:
                friendship = get_friendship_between(user.id, peer_user_id)
                if friendship is not None and friendship.status == ChatFriendship.Status.DELETED:
                    can_send = False
        if conversation.type == ChatConversation.Type.GROUP and hasattr(conversation, "group_config") and conversation.group_config.mute_all and member.role != ChatConversationMember.Role.OWNER:
            can_send = False
        return ConversationAccess(
            conversation=conversation,
            member=member,
            access_mode="member",
            can_send_message=can_send,
            capabilities=build_conversation_capabilities(
                conversation,
                member,
                access_mode="member",
                can_send_message=can_send,
            ),
        )

    if conversation.type == ChatConversation.Type.GROUP:
        former_member = get_member(conversation, user.id, active_only=False)
        if former_member and former_member.status in {ChatConversationMember.Status.LEFT, ChatConversationMember.Status.REMOVED}:
            return ConversationAccess(
                conversation=conversation,
                member=former_member,
                access_mode="former_member_readonly",
                can_send_message=False,
                capabilities=build_conversation_capabilities(
                    conversation,
                    former_member,
                    access_mode="former_member_readonly",
                    can_send_message=False,
                ),
            )

    if user_can_stealth_inspect(user):
        return ConversationAccess(
            conversation=conversation,
            member=None,
            access_mode="stealth_readonly",
            can_send_message=False,
            capabilities=build_conversation_capabilities(
                conversation,
                None,
                access_mode="stealth_readonly",
                can_send_message=False,
            ),
        )

    raise PermissionDenied(get_conversation_denied_detail(conversation, user.id))


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