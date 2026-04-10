from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.domain.access import get_member
from chat.domain.common import to_serializable_datetime, user_brief
from chat.domain.conversations import create_or_restore_group_member, ensure_direct_conversation, recalculate_member_count
from chat.domain.group_policies import ensure_user_can_invite, require_group_member_manager
from chat.domain.messaging import mute_member_until
from chat.domain.serialization import serialize_conversation, serialize_group_config
from chat.application.commands.realtime import execute_send_text_message_command
from chat.infrastructure.repositories import (
    create_pending_group_application_request,
    direct_conversation_has_group_invitation,
    get_active_direct_conversation_by_pair,
    get_active_group_conversation,
    get_active_member,
    get_active_user,
    get_group_join_request_with_context,
    get_pending_group_join_request,
    list_active_member_user_ids_by_roles,
    list_active_members,
)
from chat.models import ChatConversation, ChatConversationMember, ChatGroupJoinRequest, build_pair_key
from chat.infrastructure.event_bus import notify_chat_conversation_updated, notify_chat_group_join_request_updated, notify_chat_system_notice


User = get_user_model()


def _serialize_join_request_event(join_request: ChatGroupJoinRequest) -> dict:
    return {
        "id": join_request.id,
        "conversation_id": join_request.conversation_id,
        "status": join_request.status,
        "target_user": user_brief(join_request.target_user),
        "created_at": to_serializable_datetime(join_request.created_at),
    }


def _build_group_invitation_payload(conversation: ChatConversation, inviter) -> dict:
    return {
        "conversation_id": conversation.id,
        "group_name": conversation.name,
        "group_avatar": conversation.avatar,
        "member_count": conversation.member_count_cache,
        "join_approval_required": bool(getattr(conversation.group_config, "join_approval_required", False)),
        "inviter": user_brief(inviter),
    }


def _get_group_admin_user_ids(conversation: ChatConversation) -> set[int]:
    return list_active_member_user_ids_by_roles(conversation, [ChatConversationMember.Role.OWNER, ChatConversationMember.Role.ADMIN])


def _notify_group_conversation_to_active_members(conversation: ChatConversation) -> None:
    active_members = list_active_members(conversation)
    for member in active_members:
        notify_chat_conversation_updated(member.user_id, serialize_conversation(conversation, member.user))


def execute_invite_group_member_command(current_user, conversation_id: int, target_user_id: int) -> tuple[dict, int]:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    member = get_member(conversation, current_user.id, active_only=True)
    if member is None:
        raise PermissionError("当前无权邀请成员")
    target_user = get_active_user(target_user_id)
    if target_user is None:
        raise User.DoesNotExist()
    if target_user.id == current_user.id:
        raise ValueError("不能邀请自己")
    if get_member(conversation, target_user.id, active_only=True):
        raise ValueError("目标用户已在群中")
    ensure_user_can_invite(member, conversation.group_config)
    direct_conversation = ensure_direct_conversation(current_user, target_user)
    invitation_payload = _build_group_invitation_payload(conversation, current_user)
    message_payload = execute_send_text_message_command(
        current_user,
        direct_conversation.id,
        content="邀请你加入群聊",
        extra_payload={"group_invitation": invitation_payload},
        emit_events=True,
    )
    return {
        "mode": "message_sent",
        "detail": "群邀请消息已发送",
        "direct_conversation": {"id": direct_conversation.id, "type": direct_conversation.type},
        "message": message_payload["message"],
    }, 200


def execute_apply_group_invitation_command(current_user, conversation_id: int, inviter_user_id: int) -> tuple[dict, int]:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    inviter_user = get_active_user(inviter_user_id)
    if inviter_user is None:
        raise User.DoesNotExist()
    if get_member(conversation, current_user.id, active_only=True):
        raise ValueError("你已在群聊中")
    direct_conversation = get_active_direct_conversation_by_pair(build_pair_key(current_user.id, inviter_user.id))
    invitation_exists = direct_conversation_has_group_invitation(
        direct_conversation=direct_conversation,
        inviter_user_id=inviter_user.id,
        conversation_id=conversation.id,
    )
    if not invitation_exists:
        raise PermissionError("该群邀请已失效")
    pending_request = get_pending_group_join_request(conversation, current_user)
    if pending_request is not None:
        return {
            "mode": "pending_approval",
            "detail": "你已提交过入群申请",
            "join_request": {"id": pending_request.id, "status": pending_request.status},
        }, 200
    if getattr(conversation.group_config, "join_approval_required", False):
        join_request = create_pending_group_application_request(
            conversation,
            current_user,
            inviter=current_user,
        )
        for admin_user_id in _get_group_admin_user_ids(conversation):
            notify_chat_group_join_request_updated(admin_user_id, _serialize_join_request_event(join_request))
        return {
            "mode": "pending_approval",
            "detail": "入群申请已提交，等待群管理员审批",
            "join_request": {"id": join_request.id, "status": join_request.status},
        }, 200
    created_member = create_or_restore_group_member(conversation, current_user)
    _notify_group_conversation_to_active_members(conversation)
    notify_chat_system_notice(current_user.id, "你已加入群聊", {"conversation_id": conversation.id})
    return {
        "mode": "joined",
        "detail": "已加入群聊",
        "conversation": serialize_conversation(conversation, current_user),
        "member": {"user_id": created_member.user_id, "status": created_member.status},
    }, 200


def execute_leave_group_conversation_command(current_user, conversation_id: int) -> dict:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    member = get_member(conversation, current_user.id, active_only=True)
    if member is None:
        raise ValueError("当前不在该群聊中")
    member.status = ChatConversationMember.Status.LEFT
    member.left_at = timezone.now()
    member.show_in_list = False
    member.save(update_fields=["status", "left_at", "show_in_list", "updated_at"])
    recalculate_member_count(conversation)
    return {"detail": "已退出群聊", "conversation_id": conversation.id}


def execute_remove_group_member_command(current_user, conversation_id: int, user_id: int) -> dict:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    actor_member = get_member(conversation, current_user.id, active_only=True)
    if actor_member is None:
        raise PermissionError("当前无权操作该群聊")
    require_group_member_manager(actor_member)
    target_member = get_member(conversation, user_id, active_only=True)
    if target_member is None:
        raise ChatConversationMember.DoesNotExist()
    if target_member.role == ChatConversationMember.Role.OWNER:
        raise ValueError("不能移除群主")
    target_member.status = ChatConversationMember.Status.REMOVED
    target_member.removed_at = timezone.now()
    target_member.removed_by = current_user
    target_member.show_in_list = False
    target_member.save(update_fields=["status", "removed_at", "removed_by", "show_in_list", "updated_at"])
    recalculate_member_count(conversation)
    notify_chat_system_notice(target_member.user_id, "你已被移出群聊", {"conversation_id": conversation.id, "notice_type": "group_member_removed"})
    return {"detail": "已移出群成员", "conversation_id": conversation.id, "user_id": user_id}


def execute_update_group_member_role_command(current_user, conversation_id: int, user_id: int, role: str) -> dict:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    actor_member = get_member(conversation, current_user.id, active_only=True)
    if actor_member is None or actor_member.role != ChatConversationMember.Role.OWNER:
        raise PermissionError("仅群主可设置成员角色")
    target_member = get_member(conversation, user_id, active_only=True)
    if target_member is None:
        raise ChatConversationMember.DoesNotExist()
    if target_member.role == ChatConversationMember.Role.OWNER:
        raise ValueError("不能修改群主角色")
    target_member.role = role
    target_member.save(update_fields=["role", "updated_at"])
    return {"detail": "成员角色已更新", "conversation_id": conversation.id, "user_id": user_id, "role": target_member.role}


def execute_mute_group_member_command(current_user, conversation_id: int, user_id: int, mute_minutes: int, reason: str = "") -> dict:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None:
        raise ChatConversation.DoesNotExist()
    actor_member = get_member(conversation, current_user.id, active_only=True)
    if actor_member is None:
        raise PermissionError("当前无权操作该群聊")
    require_group_member_manager(actor_member)
    target_member = get_member(conversation, user_id, active_only=True)
    if target_member is None:
        raise ChatConversationMember.DoesNotExist()
    if target_member.role == ChatConversationMember.Role.OWNER:
        raise ValueError("不能禁言群主")
    if mute_minutes <= 0:
        target_member.mute_until = None
        target_member.mute_reason = ""
        target_member.save(update_fields=["mute_until", "mute_reason", "updated_at"])
        return {"detail": "成员已解除禁言", "conversation_id": conversation.id, "user_id": user_id, "mute_until": None}
    target_member.mute_until = mute_member_until(mute_minutes)
    target_member.mute_reason = reason
    target_member.save(update_fields=["mute_until", "mute_reason", "updated_at"])
    return {"detail": "成员已被禁言", "conversation_id": conversation.id, "user_id": user_id, "mute_until": target_member.mute_until}


def execute_update_group_config_command(current_user, conversation_id: int, data: dict) -> dict:
    conversation = get_active_group_conversation(conversation_id)
    if conversation is None or not hasattr(conversation, "group_config"):
        raise ChatConversation.DoesNotExist()
    actor_member = get_member(conversation, current_user.id, active_only=True)
    if actor_member is None:
        raise PermissionError("当前无权操作该群聊")
    require_group_member_manager(actor_member)
    conversation_fields = []
    if "name" in data:
        conversation.name = data["name"]
        conversation_fields.append("name")
    if "avatar" in data:
        conversation.avatar = data["avatar"]
        conversation_fields.append("avatar")
    if conversation_fields:
        conversation.save(update_fields=[*conversation_fields, "updated_at"])
    for field in ["join_approval_required", "allow_member_invite", "max_members", "mute_all"]:
        if field in data:
            setattr(conversation.group_config, field, data[field])
    conversation.group_config.save()
    active_members = list_active_members(conversation)
    for member in active_members:
        notify_chat_conversation_updated(member.user_id, serialize_conversation(conversation, member.user))
    return {"detail": "群配置已更新", "conversation": serialize_conversation(conversation, current_user), "group_config": serialize_group_config(conversation.group_config)}


def execute_handle_group_join_request_command(current_user, request_id: int, action: str, review_note: str = "") -> dict:
    join_request = get_group_join_request_with_context(request_id)
    if join_request is None:
        raise ChatGroupJoinRequest.DoesNotExist()
    if join_request.status != ChatGroupJoinRequest.Status.PENDING:
        raise ValueError("当前审批记录不可再处理")
    actor_member = get_member(join_request.conversation, current_user.id, active_only=True)
    now = timezone.now()
    if action in {"approve", "reject"}:
        if actor_member is None:
            raise PermissionError("当前无权处理审批")
        require_group_member_manager(actor_member)
        join_request.status = ChatGroupJoinRequest.Status.APPROVED if action == "approve" else ChatGroupJoinRequest.Status.REJECTED
        join_request.reviewer = current_user
        join_request.review_note = review_note
        join_request.reviewed_at = now
        join_request.save(update_fields=["status", "reviewer", "review_note", "reviewed_at", "updated_at"])
        if action == "approve":
            create_or_restore_group_member(join_request.conversation, join_request.target_user)
            _notify_group_conversation_to_active_members(join_request.conversation)
            notify_chat_system_notice(join_request.target_user_id, "你已加入群聊", {"conversation_id": join_request.conversation_id})
    else:
        if join_request.inviter_id != current_user.id:
            raise PermissionError("仅邀请人可取消审批")
        join_request.status = ChatGroupJoinRequest.Status.CANCELED
        join_request.reviewer = current_user
        join_request.review_note = review_note
        join_request.reviewed_at = now
        join_request.save(update_fields=["status", "reviewer", "review_note", "reviewed_at", "updated_at"])
    notify_user_ids = _get_group_admin_user_ids(join_request.conversation) | {join_request.target_user_id, join_request.inviter_id}
    for target_user_id in notify_user_ids:
        notify_chat_group_join_request_updated(target_user_id, _serialize_join_request_event(join_request))
    return {"detail": "群审批已处理", "join_request": {"id": join_request.id, "status": join_request.status}}