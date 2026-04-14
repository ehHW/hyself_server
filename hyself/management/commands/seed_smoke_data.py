from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from chat.models import ChatConversation, ChatConversationMember, ChatGroupConfig
from user.models import Permission, Role, User
from user.signals import ensure_default_permissions_synced

SMOKE_PASSWORD = "SmokePass123!"
SHARED_CONVERSATION_NAME = "chat_regression_shared_room"
SMOKE_ROLE_PERMISSION_CODES = {
    "资源只读": [
        "file.view_resource",
    ],
    "聊天管理员": [
        "chat.view_conversation",
        "chat.send_message",
        "chat.send_attachment",
        "chat.create_group",
        "chat.manage_group",
        "chat.add_friend",
        "chat.delete_friend",
        "chat.hide_conversation",
        "chat.pin_conversation",
        "chat.forward_message",
        "chat.revoke_message",
        "chat.delete_message",
        "chat.restore_revoked_message",
    ],
    "游戏只读": [
        "game.view_leaderboard",
    ],
}
SMOKE_ROLE_DESCRIPTIONS = {
    "资源只读": "浏览器烟雾测试临时角色：仅可查看资源中心",
    "聊天管理员": "浏览器烟雾测试临时角色：验证聊天相关权限矩阵",
    "游戏只读": "浏览器烟雾测试临时角色：仅可查看排行榜",
}


class Command(BaseCommand):
    help = "创建浏览器烟雾回归所需的角色、账号和共享群聊数据。"

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_default_permissions_synced()
        roles = {role.name: role for role in Role.objects.filter(name="普通用户")}
        for role_name, permission_codes in SMOKE_ROLE_PERMISSION_CODES.items():
            role = Role.all_objects.filter(name=role_name).first()
            if role is None:
                role = Role.all_objects.create(
                    name=role_name,
                    description=SMOKE_ROLE_DESCRIPTIONS.get(role_name, ""),
                )
            else:
                updates = []
                next_description = SMOKE_ROLE_DESCRIPTIONS.get(role_name, "")
                if role.description != next_description:
                    role.description = next_description
                    updates.append("description")
                if role.deleted_at is not None:
                    role.deleted_at = None
                    updates.append("deleted_at")
                if updates:
                    updates.append("updated_at")
                    role.save(update_fields=updates)
            role.permissions.set(Permission.objects.filter(code__in=permission_codes))
            roles[role_name] = role

        smoke_users = [
            ("admin", True, []),
            ("rbac_smoke_normal", False, [roles.get("普通用户")]),
            ("rbac_smoke_resource", False, [roles.get("资源只读")]),
            ("rbac_smoke_chat_admin", False, [roles.get("聊天管理员")]),
            ("rbac_smoke_game", False, [roles.get("游戏只读")]),
            ("rbac_smoke_super", True, []),
        ]

        created_users: dict[str, User] = {}
        for username, is_superuser, assigned_roles in smoke_users:
            user = User.all_objects.filter(username=username).first()
            if user is None:
                user = User.all_objects.create_user(
                    username=username,
                    password=SMOKE_PASSWORD,
                    display_name=username,
                    is_superuser=is_superuser,
                    is_staff=is_superuser,
                )
                self.stdout.write(self.style.SUCCESS(f"创建烟雾账号: {username}"))
            else:
                updates = []
                if user.deleted_at is not None:
                    user.deleted_at = None
                    updates.append("deleted_at")
                if not user.is_active:
                    user.is_active = True
                    updates.append("is_active")
                if user.display_name != username:
                    user.display_name = username
                    updates.append("display_name")
                if user.is_superuser != is_superuser:
                    user.is_superuser = is_superuser
                    updates.append("is_superuser")
                if user.is_staff != is_superuser:
                    user.is_staff = is_superuser
                    updates.append("is_staff")
                if updates:
                    updates.append("updated_at")
                    user.save(update_fields=updates)
                user.set_password(SMOKE_PASSWORD)
                user.save(update_fields=["password"])

            valid_roles = [role for role in assigned_roles if role is not None]
            if valid_roles:
                user.roles.set(valid_roles)
            elif not is_superuser:
                user.roles.clear()
            created_users[username] = user

        conversation = ChatConversation.all_objects.filter(name=SHARED_CONVERSATION_NAME, type=ChatConversation.Type.GROUP).first()
        owner = created_users["rbac_smoke_super"]
        if conversation is None:
            conversation = ChatConversation.all_objects.create(
                type=ChatConversation.Type.GROUP,
                status=ChatConversation.Status.ACTIVE,
                name=SHARED_CONVERSATION_NAME,
                owner=owner,
            )
            ChatGroupConfig.objects.create(
                conversation=conversation,
                join_approval_required=False,
                allow_member_invite=True,
            )
            self.stdout.write(self.style.SUCCESS(f"创建共享群聊: {SHARED_CONVERSATION_NAME}"))
        else:
            updates = []
            if conversation.deleted_at is not None:
                conversation.deleted_at = None
                updates.append("deleted_at")
            if conversation.status != ChatConversation.Status.ACTIVE:
                conversation.status = ChatConversation.Status.ACTIVE
                updates.append("status")
            if conversation.owner_id != owner.id:
                conversation.owner = owner
                updates.append("owner")
            if updates:
                updates.append("updated_at")
                conversation.save(update_fields=updates)
            ChatGroupConfig.objects.get_or_create(
                conversation=conversation,
                defaults={
                    "join_approval_required": False,
                    "allow_member_invite": True,
                },
            )

        members = {
            created_users["rbac_smoke_super"].id: ChatConversationMember.Role.OWNER,
            created_users["rbac_smoke_chat_admin"].id: ChatConversationMember.Role.ADMIN,
        }
        for user_id, role in members.items():
            membership = ChatConversationMember.objects.filter(conversation=conversation, user_id=user_id).first()
            if membership is None:
                ChatConversationMember.objects.create(
                    conversation=conversation,
                    user_id=user_id,
                    role=role,
                    status=ChatConversationMember.Status.ACTIVE,
                    show_in_list=True,
                )
            else:
                updates = []
                if membership.status != ChatConversationMember.Status.ACTIVE:
                    membership.status = ChatConversationMember.Status.ACTIVE
                    updates.append("status")
                if membership.role != role:
                    membership.role = role
                    updates.append("role")
                if not membership.show_in_list:
                    membership.show_in_list = True
                    updates.append("show_in_list")
                if updates:
                    updates.append("updated_at")
                    membership.save(update_fields=updates)

        self.stdout.write(self.style.SUCCESS("烟雾回归数据已准备完成"))
