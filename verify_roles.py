#!/usr/bin/env python
"""
验证基础角色创建情况
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hyself_server.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from user.models import Role, Permission

# 查询所有非删除的角色
roles = Role.objects.filter(deleted_at__isnull=True).order_by('id')

print("=" * 80)
print("已创建的角色列表")
print("=" * 80)

for role in roles:
    permissions = role.permissions.all()
    print(f"\n【ID: {role.id}】{role.name}")
    print(f"  描述: {role.description}")
    print(f"  权限数: {permissions.count()}")
    if permissions.count() > 0:
        print("  权限列表:")
        for perm in permissions:
            print(f"    - {perm.code} ({perm.name})")

print("\n" + "=" * 80)
print(f"总共 {roles.count()} 个角色")
print("=" * 80)
