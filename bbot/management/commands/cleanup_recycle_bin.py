from django.core.management.base import BaseCommand

from bbot_server.cron_jobs.cleanup_recycle_bin import run


class Command(BaseCommand):
    help = "清理回收站中超过 30 天的文件（物理删除 + 数据库删除）"

    def handle(self, *args, **options):
        result = run()
        self.stdout.write(
            self.style.SUCCESS(
                "cleanup_recycle_bin done: "
                f"removed_db_files={result['removed_db_files']}, "
                f"removed_db_dirs={result['removed_db_dirs']}, "
                f"removed_disk_files={result['removed_disk_files']}"
            )
        )
