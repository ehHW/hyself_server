from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from chat.models import ChatConversation, ChatConversationMember, ChatGroupConfig, build_pair_key


User = get_user_model()


def recalculate_member_count(conversation: ChatConversation) -> int:
    count = ChatConversationMember.objects.filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).count()
    if conversation.member_count_cache != count:
        conversation.member_count_cache = count
        conversation.save(update_fields=["member_count_cache", "updated_at"])
    return count


def ensure_direct_conversation(user_a, user_b) -> ChatConversation:
    pair_key = build_pair_key(user_a.id, user_b.id)
    with transaction.atomic():
        conversation = ChatConversation.all_objects.select_for_update().filter(direct_pair_key=pair_key).first()
        if conversation is None:
            conversation = ChatConversation.objects.create(
                type=ChatConversation.Type.DIRECT,
                direct_pair_key=pair_key,
                status=ChatConversation.Status.ACTIVE,
                name="",
            )
        else:
            if conversation.deleted_at is not None:
                conversation.restore()
            if conversation.status != ChatConversation.Status.ACTIVE:
                conversation.status = ChatConversation.Status.ACTIVE
                conversation.save(update_fields=["status", "updated_at"])

        for current_user in [user_a, user_b]:
            membership = ChatConversationMember.objects.filter(conversation=conversation, user=current_user).first()
            if membership is None:
                ChatConversationMember.objects.create(
                    conversation=conversation,
                    user=current_user,
                    role=ChatConversationMember.Role.MEMBER,
                    status=ChatConversationMember.Status.ACTIVE,
                    joined_at=timezone.now(),
                    show_in_list=True,
                )
            else:
                membership.status = ChatConversationMember.Status.ACTIVE
                membership.left_at = None
                membership.removed_at = None
                membership.removed_by = None
                membership.show_in_list = True
                membership.save(update_fields=["status", "left_at", "removed_at", "removed_by", "show_in_list", "updated_at"])

        recalculate_member_count(conversation)
    return conversation


def create_group_conversation(owner, *, name: str, member_users: list[User], join_approval_required: bool, allow_member_invite: bool) -> ChatConversation:
    with transaction.atomic():
        conversation = ChatConversation.objects.create(
            type=ChatConversation.Type.GROUP,
            name=name,
            owner=owner,
            status=ChatConversation.Status.ACTIVE,
        )
        ChatGroupConfig.objects.create(
            conversation=conversation,
            join_approval_required=join_approval_required,
            allow_member_invite=allow_member_invite,
        )
        ChatConversationMember.objects.create(
            conversation=conversation,
            user=owner,
            role=ChatConversationMember.Role.OWNER,
            status=ChatConversationMember.Status.ACTIVE,
            joined_at=timezone.now(),
            show_in_list=True,
        )
        for member_user in member_users:
            if member_user.id == owner.id:
                continue
            ChatConversationMember.objects.get_or_create(
                conversation=conversation,
                user=member_user,
                defaults={
                    "role": ChatConversationMember.Role.MEMBER,
                    "status": ChatConversationMember.Status.ACTIVE,
                    "joined_at": timezone.now(),
                    "show_in_list": True,
                },
            )
        recalculate_member_count(conversation)
    return conversation


def create_or_restore_group_member(conversation: ChatConversation, target_user, *, role: str = ChatConversationMember.Role.MEMBER) -> ChatConversationMember:
    membership = ChatConversationMember.objects.filter(conversation=conversation, user=target_user).first()
    if membership is None:
        membership = ChatConversationMember.objects.create(
            conversation=conversation,
            user=target_user,
            role=role,
            status=ChatConversationMember.Status.ACTIVE,
            joined_at=timezone.now(),
            show_in_list=True,
        )
    else:
        membership.role = role if membership.role != ChatConversationMember.Role.OWNER else membership.role
        membership.status = ChatConversationMember.Status.ACTIVE
        membership.left_at = None
        membership.removed_at = None
        membership.removed_by = None
        membership.show_in_list = True
        membership.save(update_fields=["role", "status", "left_at", "removed_at", "removed_by", "show_in_list", "updated_at"])
    recalculate_member_count(conversation)
    return membership