from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from chat.application.commands.delivery import build_message_delivery_payloads, emit_message_delivery_events
from chat.application.commands.message_payloads import build_asset_payload, build_reply_payload_from_message
from bbot.application.services.asset_references import create_chat_attachment_asset_reference
from bbot.models import Asset, AssetReference
from chat.domain.access import get_conversation_access
from chat.domain.friendships import get_active_friendship_between
from chat.domain.messaging import create_message
from chat.infrastructure.repositories import get_active_conversation, get_asset_reference_with_asset, get_conversation_message, get_other_active_member
from chat.models import ChatConversation, ChatMessage
from utils.upload import media_url


def _resolve_asset_message_type(media_type: str) -> str:
    if media_type in {Asset.MediaType.IMAGE, Asset.MediaType.AVATAR}:
        return ChatMessage.MessageType.IMAGE
    return ChatMessage.MessageType.FILE


def _build_reply_payload(conversation: ChatConversation, quoted_message_id: int | None) -> dict | None:
    if not quoted_message_id:
        return None
    quoted_message = get_conversation_message(conversation, quoted_message_id)
    if quoted_message is None:
        raise ValidationError({"quoted_message_id": "引用消息不存在"})
    return build_reply_payload_from_message(quoted_message)


def execute_send_asset_message_command(
    user,
    conversation_id: int,
    *,
    source_asset_reference_id: int,
    quoted_message_id: int | None = None,
    extra_payload: dict | None = None,
    emit_events: bool = True,
) -> dict:
    conversation = get_active_conversation(conversation_id)
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})

    access = get_conversation_access(user, conversation)
    if access.access_mode != "member" or not access.can_send_message:
        raise PermissionDenied("当前无权发送消息")

    if conversation.type == ChatConversation.Type.DIRECT:
        peer_member = get_other_active_member(conversation, exclude_user_id=user.id)
        if peer_member is not None and get_active_friendship_between(user.id, peer_member.user_id) is None:
            raise PermissionDenied("你们还不是好友，当前私聊暂不支持发送附件")

    source_reference = get_asset_reference_with_asset(source_asset_reference_id)
    if source_reference is None or source_reference.asset is None:
        raise ValidationError({"asset_reference_id": "资产引用不存在或未绑定文件"})
    if source_reference.ref_type == AssetReference.RefType.DIRECTORY:
        raise ValidationError({"asset_reference_id": "目录不能作为聊天附件发送"})
    if source_reference.status != AssetReference.Status.ACTIVE or source_reference.deleted_at is not None:
        raise ValidationError({"asset_reference_id": "资产引用当前不可发送"})
    if source_reference.owner_user_id and source_reference.owner_user_id != user.id:
        raise PermissionDenied("当前无权发送该资产")

    chat_reference = create_chat_attachment_asset_reference(
        source_reference=source_reference,
        owner_user=user,
        conversation_id=conversation.id,
    )

    if access.member is not None and not access.member.show_in_list:
        access.member.show_in_list = True
        access.member.save(update_fields=["show_in_list", "updated_at"])

    payload = build_asset_payload(chat_reference=chat_reference, source_reference=source_reference)
    payload.update(extra_payload or {})
    reply_payload = _build_reply_payload(conversation, quoted_message_id)
    if reply_payload is not None:
        payload["reply_to_message"] = reply_payload
    asset = source_reference.asset
    message = create_message(
        conversation,
        user,
        chat_reference.display_name,
        message_type=_resolve_asset_message_type(asset.media_type),
        payload=payload,
    )

    conversation, message_payload, sender_conversation, recipient_payloads = build_message_delivery_payloads(
        conversation=conversation,
        sender_user=user,
        message=message,
    )

    if emit_events:
        emit_message_delivery_events(
            conversation=conversation,
            sender_user=user,
            message_payload=message_payload,
            sender_conversation=sender_conversation,
            recipient_payloads=recipient_payloads,
        )

    return {
        "detail": "附件消息已发送",
        "conversation_id": conversation.id,
        "message": message_payload,
        "conversation": sender_conversation,
        "sender_conversation": sender_conversation,
        "recipients": recipient_payloads,
        "asset_reference": {
            "id": chat_reference.id,
            "asset_id": chat_reference.asset_id,
            "ref_domain": chat_reference.ref_domain,
            "ref_type": chat_reference.ref_type,
        },
    }