from __future__ import annotations

from chat.models import ChatConversation, ChatConversationMember


def get_active_member(conversation: ChatConversation, user_id: int) -> ChatConversationMember | None:
    return ChatConversationMember.objects.filter(conversation=conversation, user_id=user_id, status=ChatConversationMember.Status.ACTIVE).first()


def get_other_active_member(conversation: ChatConversation, *, exclude_user_id: int) -> ChatConversationMember | None:
    return (
        ChatConversationMember.objects.filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE)
        .exclude(user_id=exclude_user_id)
        .first()
    )


def list_recipient_members(conversation: ChatConversation, *, exclude_user_id: int) -> list[ChatConversationMember]:
    return list(
        ChatConversationMember.objects.select_related("user")
        .filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE)
        .exclude(user_id=exclude_user_id)
    )


def list_active_members(conversation: ChatConversation) -> list[ChatConversationMember]:
    return list(
        ChatConversationMember.objects.select_related("user")
        .filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE)
    )


def list_active_member_user_ids_by_roles(conversation: ChatConversation, roles: list[str]) -> set[int]:
    return set(
        ChatConversationMember.objects.filter(
            conversation=conversation,
            status=ChatConversationMember.Status.ACTIVE,
            role__in=roles,
        ).values_list("user_id", flat=True)
    )


def list_other_active_member_user_ids(conversation: ChatConversation, *, exclude_user_id: int) -> list[int]:
    return list(
        ChatConversationMember.objects.filter(
            conversation=conversation,
            status=ChatConversationMember.Status.ACTIVE,
        ).exclude(user_id=exclude_user_id).values_list("user_id", flat=True)
    )


def reveal_hidden_members(members: list[ChatConversationMember]) -> None:
    hidden_member_ids = [item.pk for item in members if not item.show_in_list]
    if hidden_member_ids:
        ChatConversationMember.objects.filter(pk__in=hidden_member_ids).update(show_in_list=True)


def refresh_member(member_id: int) -> ChatConversationMember:
    return ChatConversationMember.objects.select_related("user").get(pk=member_id)