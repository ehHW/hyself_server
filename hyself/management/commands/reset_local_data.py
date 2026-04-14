from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import DEFAULT_USER_ROLE_NAME, Role, SYSTEM_ADMIN_ROLE_NAME, User
from user.signals import ensure_default_permissions_synced


BASELINE_USERS = [
    ("user01", "普通用户1"),
    ("user02", "普通用户2"),
    ("user03", "普通用户3"),
    ("user04", "普通用户4"),
    ("user05", "普通用户5"),
]


class Command(BaseCommand):
    help = "清空本地开发数据与上传文件，并重建 admin、5 个普通用户与 3 个正式角色。"

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="确认执行破坏性重置")
        parser.add_argument("--admin-password", default="", help="指定 admin 密码；不传则自动生成")
        parser.add_argument("--user-password", default="", help="指定 5 个普通用户统一密码；不传则自动生成")

    def _generate_password(self) -> str:
        return secrets.token_urlsafe(18)

    def _clear_upload_root(self) -> None:
        upload_root = Path(settings.MEDIA_ROOT)
        upload_root.mkdir(parents=True, exist_ok=True)
        for child in upload_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)

    @transaction.atomic
    def _bootstrap_users(self, admin_password: str, user_password: str) -> None:
        ensure_default_permissions_synced()
        default_role = Role.all_objects.get(name=DEFAULT_USER_ROLE_NAME)
        Role.all_objects.get(name=SYSTEM_ADMIN_ROLE_NAME)

        admin_user = User.objects.create_superuser(
            username="admin",
            password=admin_password,
            display_name="admin",
            email="",
        )
        admin_user.roles.add(Role.all_objects.get(name="超级管理员"))

        for username, display_name in BASELINE_USERS:
            user = User.objects.create_user(
                username=username,
                password=user_password,
                display_name=display_name,
                email="",
                is_active=True,
            )
            user.roles.add(default_role)

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("这是破坏性操作，请显式传入 --yes")

        admin_password = str(options.get("admin_password") or "").strip() or self._generate_password()
        user_password = str(options.get("user_password") or "").strip() or self._generate_password()

        self.stdout.write(self.style.WARNING("开始清空本地数据库数据..."))
        call_command("flush", interactive=False, verbosity=0)

        self.stdout.write(self.style.WARNING("开始清空 uploads 目录..."))
        self._clear_upload_root()

        self.stdout.write(self.style.WARNING("开始重建角色与基线用户..."))
        self._bootstrap_users(admin_password, user_password)

        self.stdout.write(self.style.SUCCESS("本地数据重置完成"))
        self.stdout.write(f"admin username: admin")
        self.stdout.write(f"admin password: {admin_password}")
        self.stdout.write(f"normal users: {', '.join(username for username, _ in BASELINE_USERS)}")
        self.stdout.write(f"normal password: {user_password}")