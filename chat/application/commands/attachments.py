from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from bbot.models import Asset, AssetReference
from chat.domain.access import get_conversation_access
from chat.domain.friendships import get_active_friendship_between
from chat.domain.messaging import create_message, get_total_unread_count
from chat.domain.serialization import serialize_conversation, serialize_message
from chat.models import ChatConversation, ChatConversationMember, ChatMessage
from utils.upload import media_url
from ws.events import notify_chat_conversation_updated, notify_chat_new_message, notify_chat_unread_updated


def _resolve_asset_message_type(media_type: str) -> str:
    if media_type in {Asset.MediaType.IMAGE, Asset.MediaType.AVATAR}:
        return ChatMessage.MessageType.IMAGE
    return ChatMessage.MessageType.FILE


def _build_reply_payload(conversation: ChatConversation, quoted_message_id: int | None) -> dict | None:
    if not quoted_message_id:
        return None
    quoted_message = ChatMessage.objects.select_related("sender").filter(conversation=conversation, id=quoted_message_id).first()
    if quoted_message is None:
        raise ValidationError({"quoted_message_id": "引用消息不存在"})
    sender_name = "系统"
    if quoted_message.sender is not None:
        sender_name = quoted_message.sender.display_name or quoted_message.sender.username
    preview = quoted_message.content or ""
    if quoted_message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        preview = str((quoted_message.payload or {}).get("display_name") or quoted_message.content or "附件")
    elif quoted_message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        preview = str(((quoted_message.payload or {}).get("chat_record") or {}).get("title") or quoted_message.content or "聊天记录")
    return {
        "id": quoted_message.id,
        "sequence": quoted_message.sequence,
        "message_type": quoted_message.message_type,
        "sender_name": sender_name,
        "content_preview": preview[:120],
    }


def execute_send_asset_message_command(
    user,
    conversation_id: int,
    *,
    source_asset_reference_id: int,
    quoted_message_id: int | None = None,
    extra_payload: dict | None = None,
    emit_events: bool = True,
) -> dict:
    conversation = ChatConversation.objects.select_related("owner", "group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        raise ValidationError({"detail": "会话不存在"})

    access = get_conversation_access(user, conversation)
    if access.access_mode != "member" or not access.can_send_message:
        raise PermissionDenied("当前无权发送消息")

    if conversation.type == ChatConversation.Type.DIRECT:
        peer_member = (
            ChatConversationMember.objects.filter(
                conversation=conversation,
                status=ChatConversationMember.Status.ACTIVE,
            )
            .exclude(user_id=user.id)
            .first()
        )
        if peer_member is not None and get_active_friendship_between(user.id, peer_member.user_id) is None:
            raise PermissionDenied("你们还不是好友，当前私聊暂不支持发送附件")

    source_reference = AssetReference.objects.select_related("asset").filter(id=source_asset_reference_id).first()
    if source_reference is None or source_reference.asset is None:
        raise ValidationError({"asset_reference_id": "资产引用不存在或未绑定文件"})
    if source_reference.ref_type == AssetReference.RefType.DIRECTORY:
        raise ValidationError({"asset_reference_id": "目录不能作为聊天附件发送"})
    if source_reference.status != AssetReference.Status.ACTIVE or source_reference.deleted_at is not None:
        raise ValidationError({"asset_reference_id": "资产引用当前不可发送"})
    if source_reference.owner_user_id and source_reference.owner_user_id != user.id:
        raise PermissionDenied("当前无权发送该资产")

    chat_reference = AssetReference.objects.create(
        asset=source_reference.asset,
        owner_user=user,
        ref_domain=AssetReference.RefDomain.CHAT,
        ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
        ref_object_id=str(conversation.id),
        display_name=source_reference.display_name or source_reference.asset.original_name,
        relative_path_cache=source_reference.relative_path_cache or source_reference.asset.storage_key,
        status=AssetReference.Status.ACTIVE,
        visibility=AssetReference.Visibility.CONVERSATION,
        extra_metadata={
            "source_asset_reference_id": source_reference.id,
            "source_ref_domain": source_reference.ref_domain,
            "source_ref_type": source_reference.ref_type,
        },
    )

    if access.member is not None and not access.member.show_in_list:
        access.member.show_in_list = True
        access.member.save(update_fields=["show_in_list", "updated_at"])

    asset = source_reference.asset
    payload = {
        "asset_reference_id": chat_reference.id,
        "source_asset_reference_id": source_reference.id,
        "display_name": chat_reference.display_name,
        "media_type": asset.media_type,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "url": media_url(asset.storage_key) if asset.storage_key and asset.storage_backend == Asset.StorageBackend.LOCAL else "",
    }
    payload.update(extra_payload or {})
    reply_payload = _build_reply_payload(conversation, quoted_message_id)
    if reply_payload is not None:
        payload["reply_to_message"] = reply_payload
    message = create_message(
        conversation,
        user,
        chat_reference.display_name,
        message_type=_resolve_asset_message_type(asset.media_type),
        payload=payload,
    )

    conversation = ChatConversation.objects.select_related("owner", "group_config").get(pk=conversation.pk)
    message_payload = serialize_message(message)
    sender_conversation = serialize_conversation(conversation, user)
    recipient_members = list(
        ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).exclude(user_id=user.id)
    )
    hidden_recipient_ids = [item.pk for item in recipient_members if not item.show_in_list]
    if hidden_recipient_ids:
        ChatConversationMember.objects.filter(pk__in=hidden_recipient_ids).update(show_in_list=True)

    recipient_payloads = []
    for recipient_member in recipient_members:
        refreshed_member = ChatConversationMember.objects.get(pk=recipient_member.pk)
        recipient_payloads.append(
            {
                "user_id": recipient_member.user_id,
                "conversation": serialize_conversation(conversation, recipient_member.user),
                "unread_count": refreshed_member.unread_count,
                "total_unread_count": get_total_unread_count(recipient_member.user),
            }
        )

    if emit_events:
        notify_chat_new_message(user.id, {"conversation_id": conversation.id, "message": message_payload})
        notify_chat_conversation_updated(user.id, sender_conversation)
        for recipient in recipient_payloads:
            notify_chat_new_message(recipient["user_id"], {"conversation_id": conversation.id, "message": message_payload})
            notify_chat_conversation_updated(recipient["user_id"], recipient["conversation"])
            notify_chat_unread_updated(recipient["user_id"], conversation.id, recipient["unread_count"], recipient["total_unread_count"])

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