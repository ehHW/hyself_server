from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from chat.models import ChatConversation, ChatConversationMember, ChatMessage


def create_message(conversation: ChatConversation, sender, content: str, client_message_id: str | None = None, message_type: str = ChatMessage.MessageType.TEXT, payload: dict | None = None, is_system: bool = False) -> ChatMessage:
    payload = payload or {}
    with transaction.atomic():
        locked_conversation = ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        current_max_sequence = ChatMessage.objects.filter(conversation=locked_conversation).aggregate(value=Max("sequence")).get("value") or 0
        next_sequence = current_max_sequence + 1
        message = ChatMessage.objects.create(
            conversation=locked_conversation,
            sequence=next_sequence,
            sender=sender,
            message_type=message_type,
            content=content,
            payload=payload,
            client_message_id=client_message_id,
            is_system=is_system,
        )
        preview = content.strip().replace("\n", " ")[:255]
        locked_conversation.last_message = message
        locked_conversation.last_message_preview = preview
        locked_conversation.last_message_at = message.created_at
        locked_conversation.save(update_fields=["last_message", "last_message_preview", "last_message_at", "updated_at"])
        queryset = ChatConversationMember.objects.filter(conversation=locked_conversation, status=ChatConversationMember.Status.ACTIVE)
        if sender is not None:
            queryset = queryset.exclude(user_id=sender.id)
        queryset.update(unread_count=F("unread_count") + 1)
    return message


def mark_conversation_read(member: ChatConversationMember, last_read_sequence: int) -> ChatConversationMember:
    target_message = ChatMessage.objects.filter(conversation=member.conversation, sequence=last_read_sequence).first()
    member.last_read_sequence = last_read_sequence
    member.last_read_message = target_message
    member.unread_count = 0
    member.save(update_fields=["last_read_sequence", "last_read_message", "unread_count", "updated_at"])
    return member


def get_total_unread_count(user) -> int:
    return int(
        ChatConversationMember.objects.filter(user=user, status=ChatConversationMember.Status.ACTIVE).aggregate(value=Sum("unread_count")).get("value")
        or 0
    )


def mute_member_until(minutes: int):
    return timezone.now() + timedelta(minutes=minutes)