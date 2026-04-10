from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.domain.serialization import serialize_conversation, serialize_message
from chat.infrastructure.event_bus import notify_chat_conversation_updated, notify_chat_message_updated
from chat.models import ChatConversationMember, ChatMessage


REVOKE_TIME_LIMIT_SECONDS = 120


def _is_revoked(message: ChatMessage) -> bool:
    revoked = (message.payload or {}).get('revoked')
    return isinstance(revoked, dict) and bool(revoked.get('revoked_at'))


def _build_original_snapshot(message: ChatMessage) -> dict:
    payload = dict(message.payload or {})
    payload.pop('revoked', None)
    return {
        'message_type': message.message_type,
        'content': message.content,
        'payload': payload,
    }


def _broadcast_message_update(message: ChatMessage) -> dict:
    message_payload = serialize_message(message)
    wrapped_payload = {
        'conversation_id': message.conversation_id,
        'message': message_payload,
    }
    for member in ChatConversationMember.objects.select_related('user').filter(conversation=message.conversation, status=ChatConversationMember.Status.ACTIVE):
        notify_chat_message_updated(member.user_id, wrapped_payload)
        notify_chat_conversation_updated(member.user_id, serialize_conversation(message.conversation, member.user))
    return message_payload


def execute_revoke_message_command(current_user, message_id: int) -> dict:
    message = ChatMessage.objects.select_related('conversation').filter(id=message_id).first()
    if message is None:
        raise ChatMessage.DoesNotExist()
    if message.sender_id != current_user.id:
        raise PermissionDenied('只能撤回自己发送的消息')
    if message.is_system:
        raise ValidationError({'detail': '系统消息不支持撤回'})
    if _is_revoked(message):
        raise ValidationError({'detail': '该消息已撤回'})
    if (timezone.now() - message.created_at).total_seconds() > REVOKE_TIME_LIMIT_SECONDS:
        raise ValidationError({'detail': '只能撤回两分钟内发送的消息'})

    payload = dict(message.payload or {})
    payload['revoked'] = {
        'revoked_at': timezone.now().isoformat(),
        'revoked_by_user_id': current_user.id,
        'can_restore_once': message.message_type in {ChatMessage.MessageType.TEXT, ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE},
        'restore_used': False,
        'original_message': _build_original_snapshot(message),
    }
    message.payload = payload
    message.save(update_fields=['payload', 'updated_at'])
    return {
        'detail': '消息已撤回',
        'message': _broadcast_message_update(message),
    }


def execute_restore_revoked_draft_command(current_user, message_id: int) -> dict:
    message = ChatMessage.objects.select_related('conversation').filter(id=message_id).first()
    if message is None:
        raise ChatMessage.DoesNotExist()
    revoked = (message.payload or {}).get('revoked')
    if not isinstance(revoked, dict) or not revoked.get('revoked_at'):
        raise ValidationError({'detail': '该消息未撤回'})
    if message.sender_id != current_user.id:
        raise PermissionDenied('当前无权恢复该消息草稿')
    if not revoked.get('can_restore_once'):
        raise ValidationError({'detail': '该消息类型暂不支持恢复编辑'})
    if revoked.get('restore_used'):
        raise ValidationError({'detail': '撤回后的编辑入口只能使用一次'})

    original_message = revoked.get('original_message')
    if not isinstance(original_message, dict):
        raise ValidationError({'detail': '原始消息内容不存在'})

    next_payload = dict(message.payload or {})
    next_revoked = dict(revoked)
    next_revoked['restore_used'] = True
    next_payload['revoked'] = next_revoked
    message.payload = next_payload
    message.save(update_fields=['payload', 'updated_at'])

    _broadcast_message_update(message)
    return {
        'detail': '已恢复到输入框',
        'draft': {
            'message_type': original_message.get('message_type'),
            'content': original_message.get('content') or '',
            'payload': original_message.get('payload') or {},
        },
    }