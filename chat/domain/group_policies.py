from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from chat.models import ChatConversationMember, ChatGroupConfig


def require_group_member_manager(member: ChatConversationMember):
    if member.role not in {ChatConversationMember.Role.OWNER, ChatConversationMember.Role.ADMIN}:
        raise PermissionDenied("当前无权执行该操作")


def ensure_user_can_invite(member: ChatConversationMember, group_config: ChatGroupConfig):
    if group_config.allow_member_invite:
        return
    require_group_member_manager(member)