from __future__ import annotations

from chat.domain.access import (
    build_discover_preview_capabilities,
    get_conversation_access,
    serialize_conversation_capabilities,
    user_can_stealth_inspect,
)
from chat.application.commands.message_payloads import build_reply_payload_from_message, is_message_revoked
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.friendships import friendship_counterparty, friendship_remark, get_active_friendship_between
from chat.domain.member_settings import get_member_preferences
from chat.infrastructure.repositories import get_latest_visible_message
from hyself.models import AssetReference
from hyself.utils.upload import media_url
from chat.models import ChatConversation, ChatConversationMember, ChatFriendRequest, ChatFriendship, ChatGroupConfig, ChatMessage, build_pair_key


def serialize_message(message: ChatMessage) -> dict:
    payload = dict(message.payload or {})
    if message.message_type in {ChatMessage.MessageType.IMAGE, ChatMessage.MessageType.FILE}:
        asset_reference_id = payload.get("source_asset_reference_id") or payload.get("asset_reference_id")
        if isinstance(asset_reference_id, int):
            reference = AssetReference.objects.select_related("asset").filter(id=asset_reference_id, deleted_at__isnull=True).first()
            asset = None if reference is None else reference.asset
            if asset is not None:
                video_processing = ((asset.extra_metadata or {}).get("video_processing") if isinstance(asset.extra_metadata, dict) else None) or {}
                payload["url"] = payload.get("url") or (media_url(asset.storage_key) if asset.storage_key and asset.storage_backend == asset.StorageBackend.LOCAL else "")
                payload["stream_url"] = str(video_processing.get("playlist_url") or payload.get("stream_url") or "")
                payload["thumbnail_url"] = str(video_processing.get("thumbnail_url") or payload.get("thumbnail_url") or "")
                payload["processing_status"] = str(video_processing.get("status") or payload.get("processing_status") or "")
                payload["subtitle_tracks"] = video_processing.get("subtitle_tracks") or payload.get("subtitle_tracks") or []
    reply_payload = payload.get("reply_to_message")
    if isinstance(reply_payload, dict) and isinstance(reply_payload.get("id"), int):
        reply_message = ChatMessage.objects.filter(id=reply_payload["id"]).first()
        if reply_message is not None and is_message_revoked(reply_message):
            payload["reply_to_message"] = build_reply_payload_from_message(reply_message)
    return {
        "id": message.id,
        "sequence": message.sequence,
        "client_message_id": message.client_message_id,
        "message_type": message.message_type,
        "content": message.content,
        "payload": payload,
        "is_system": message.is_system,
        "sender": None if message.sender is None else user_brief(message.sender),
        "created_at": to_serializable_datetime(message.created_at),
    }


def serialize_group_config(group_config: ChatGroupConfig | None) -> dict | None:
    if group_config is None:
        return None
    return {
        "join_approval_required": group_config.join_approval_required,
        "allow_member_invite": group_config.allow_member_invite,
        "max_members": group_config.max_members,
        "mute_all": group_config.mute_all,
    }


def serialize_conversation(conversation: ChatConversation, user) -> dict:
    access = get_conversation_access(user, conversation)
    include_hidden_messages = user_can_stealth_inspect(user)
    member = access.member
    is_member_access = access.access_mode == "member" and member is not None
    display_name = conversation.name
    avatar = conversation.avatar
    direct_target = None
    friend_remark = None
    if conversation.type == ChatConversation.Type.DIRECT and member:
        other_member = ChatConversationMember.objects.select_related("user").filter(conversation=conversation, status=ChatConversationMember.Status.ACTIVE).exclude(user_id=user.id).first()
        if other_member:
            display_name = other_member.user.display_name or other_member.user.username
            avatar = other_member.user.avatar
            direct_target = user_brief(other_member.user)
            friendship = get_active_friendship_between(user.id, other_member.user_id) or ChatFriendship.objects.filter(pair_key=build_pair_key(user.id, other_member.user_id)).first()
            friend_remark = friendship_remark(friendship, user.id) or None
    latest_visible_message = get_latest_visible_message(
        conversation,
        user_id=None if include_hidden_messages else user.id,
        include_hidden=include_hidden_messages,
    )
    last_message_preview = conversation.last_message_preview
    last_message_at = to_serializable_datetime(conversation.last_message_at)
    if latest_visible_message is not None:
        last_message_preview = _build_conversation_preview(latest_visible_message, viewer_user_id=user.id)
        last_message_at = to_serializable_datetime(latest_visible_message.created_at)
    elif member is not None:
        last_message_preview = ""

    return {
        "id": conversation.id,
        "type": conversation.type,
        "name": display_name,
        "avatar": avatar,
        "direct_target": direct_target,
        "friend_remark": friend_remark,
        "is_pinned": member.is_pinned if is_member_access else False,
        "access_mode": access.access_mode,
        "member_role": member.role if is_member_access else None,
        "show_in_list": member.show_in_list if member is not None else True,
        "unread_count": member.unread_count if is_member_access else 0,
        "last_message_preview": last_message_preview,
        "last_message_at": last_message_at,
        "member_count": conversation.member_count_cache,
        "can_send_message": access.can_send_message,
        "capabilities": serialize_conversation_capabilities(access.capabilities),
        "status": conversation.status,
        "last_read_sequence": member.last_read_sequence if is_member_access else 0,
        "member_settings": get_member_preferences(member),
        "group_config": serialize_group_config(getattr(conversation, "group_config", None)) if conversation.type == ChatConversation.Type.GROUP else None,
        "owner": None if conversation.owner is None else user_brief(conversation.owner),
    }


def serialize_discover_preview_conversation(conversation: ChatConversation) -> dict:
    return {
        "id": conversation.id,
        "type": conversation.type,
        "name": conversation.name,
        "access_mode": "discover_preview",
        "capabilities": serialize_conversation_capabilities(build_discover_preview_capabilities()),
    }


def _build_conversation_preview(message: ChatMessage, *, viewer_user_id: int) -> str:
    if is_message_revoked(message):
        if message.sender_id == viewer_user_id:
            return "你撤回了一条消息"
        if message.conversation.type == ChatConversation.Type.GROUP and message.sender is not None:
            sender_name = message.sender.display_name or message.sender.username
            return f"{sender_name} 撤回了一条消息"
        return "对方撤回了一条消息"

    if message.message_type == ChatMessage.MessageType.IMAGE:
        return str((message.payload or {}).get("display_name") or "[图片]")[:255]
    if message.message_type == ChatMessage.MessageType.FILE:
        return str((message.payload or {}).get("display_name") or "[文件]")[:255]
    if message.message_type == ChatMessage.MessageType.CHAT_RECORD:
        return str(((message.payload or {}).get("chat_record") or {}).get("title") or message.content or "聊天记录")[:255]
    return str(message.content or "").strip().replace("\n", " ")[:255]


def serialize_friend_request(friend_request: ChatFriendRequest) -> dict:
    return {
        "id": friend_request.id,
        "status": friend_request.status,
        "from_user": user_brief(friend_request.from_user),
        "to_user": user_brief(friend_request.to_user),
        "request_message": friend_request.request_message,
        "auto_accepted": friend_request.auto_accepted,
        "handled_by": None if friend_request.handled_by is None else user_brief(friend_request.handled_by),
        "handled_at": to_serializable_datetime(friend_request.handled_at),
        "created_at": to_serializable_datetime(friend_request.created_at),
    }


def serialize_friendship(friendship: ChatFriendship, current_user) -> dict:
    friend_user = friendship_counterparty(friendship, current_user.id)
    direct_conversation = ChatConversation.objects.filter(direct_pair_key=friendship.pair_key).first()
    direct_member = None if direct_conversation is None else ChatConversationMember.objects.filter(conversation=direct_conversation, user=current_user).first()
    return {
        "friendship_id": friendship.id,
        "friend_user": user_brief(friend_user),
        "accepted_at": to_serializable_datetime(friendship.accepted_at),
        "remark": friendship_remark(friendship, current_user.id),
        "direct_conversation": None if direct_conversation is None else {"id": direct_conversation.id, "show_in_list": True if direct_member is None else direct_member.show_in_list},
    }