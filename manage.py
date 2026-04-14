#!/usr/bin/env python
"""Django 管理任务命令行工具。"""
import os
import sys


def main():
    """执行管理任务。"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hyself_server.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django。请确认已安装 Django，且其位于 PYTHONPATH "
            "可访问路径中；同时请确认你已激活对应的虚拟环境。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
