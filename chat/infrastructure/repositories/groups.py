from __future__ import annotations

from chat.models import ChatConversation, ChatConversationMember, ChatGroupJoinRequest
from chat.infrastructure.repositories.conversations import get_active_group_conversation


def list_group_join_requests_for_user(user, *, conversation_id: int | None = None, status_filter: str = ""):
    queryset = ChatGroupJoinRequest.objects.select_related("conversation", "target_user", "inviter", "reviewer")
    if conversation_id:
        queryset = queryset.filter(conversation_id=conversation_id)
    queryset = queryset.filter(conversation__members__user=user, conversation__members__status=ChatConversationMember.Status.ACTIVE).distinct()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    return queryset[:100]


def get_accessible_group_members(conversation_id: int):
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        return None, []
    items = list(ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE))
    return conversation, items


def get_pending_group_join_request(conversation: ChatConversation, target_user):
    return ChatGroupJoinRequest.objects.filter(
        conversation=conversation,
        target_user=target_user,
        status=ChatGroupJoinRequest.Status.PENDING,
    ).first()


def get_group_join_request_with_context(request_id: int):
    return ChatGroupJoinRequest.objects.select_related(
        "conversation",
        "target_user",
        "inviter",
        "reviewer",
        "conversation__group_config",
    ).filter(id=request_id).first()


def create_pending_group_application_request(conversation: ChatConversation, target_user, *, inviter=None):
    return ChatGroupJoinRequest.objects.create(
        conversation=conversation,
        request_type=ChatGroupJoinRequest.RequestType.APPLICATION,
        inviter=inviter,
        target_user=target_user,
        status=ChatGroupJoinRequest.Status.PENDING,
    )


def direct_conversation_has_group_invitation(*, direct_conversation, inviter_user_id: int, conversation_id: int) -> bool:
    if direct_conversation is None:
        return False
    return direct_conversation.messages.filter(
        sender_id=inviter_user_id,
        payload__group_invitation__conversation_id=conversation_id,
    ).exists()