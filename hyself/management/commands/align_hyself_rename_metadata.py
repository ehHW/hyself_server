from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

TABLE_RENAMES = [
    ("uploaded_file", "hyself_uploaded_file"),
    ("asset", "hyself_asset"),
    ("asset_reference", "hyself_asset_reference"),
]


class Command(BaseCommand):
    help = "将旧 bbot 元数据与表名对齐到 hyself / hyself_server 命名。"

    def _table_exists(self, table_name: str) -> bool:
        with connection.cursor() as cursor:
            return table_name in connection.introspection.table_names(cursor)

    @transaction.atomic
    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            for old_name, new_name in TABLE_RENAMES:
                if self._table_exists(old_name) and not self._table_exists(new_name):
                    cursor.execute(f"ALTER TABLE `{old_name}` RENAME TO `{new_name}`")
                    self.stdout.write(self.style.SUCCESS(f"重命名表: {old_name} -> {new_name}"))

            cursor.execute("UPDATE django_migrations SET app = %s WHERE app = %s", ["hyself", "bbot"])
            cursor.execute("UPDATE django_content_type SET app_label = %s WHERE app_label = %s", ["hyself", "bbot"])

        self.stdout.write(self.style.SUCCESS("Django 迁移与 content type 元数据已对齐到 hyself"))
