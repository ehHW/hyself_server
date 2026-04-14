from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from chat.models import ChatConversation
from user.models import Role, User


SMOKE_USERNAMES = [
    "rbac_smoke_normal",
    "rbac_smoke_resource",
    "rbac_smoke_chat_admin",
    "rbac_smoke_game",
    "rbac_smoke_super",
]
SMOKE_ROLE_NAMES = ["资源只读", "聊天管理员", "游戏只读"]
SHARED_CONVERSATION_NAME = "chat_regression_shared_room"


class Command(BaseCommand):
    help = "删除浏览器烟雾测试账号、临时角色和共享群聊。"

    @transaction.atomic
    def handle(self, *args, **options):
        deleted_users = 0
        for user in User.all_objects.filter(username__in=SMOKE_USERNAMES):
            user.roles.clear()
            User.all_objects.filter(id=user.id).delete()
            deleted_users += 1

        ChatConversation.all_objects.filter(name=SHARED_CONVERSATION_NAME).delete()

        deleted_roles = 0
        for role in Role.all_objects.filter(name__in=SMOKE_ROLE_NAMES):
            role.permissions.clear()
            Role.all_objects.filter(id=role.id).delete()
            deleted_roles += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"烟雾测试数据已清理: users={deleted_users}, roles={deleted_roles}, conversation={SHARED_CONVERSATION_NAME}"
            )
        )