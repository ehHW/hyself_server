#!/usr/bin/env python
"""
初始化基础角色脚本
"""
import os
import sys
import django

# 配置Django环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hyself_server.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from user.models import Permission, Role

def init_basic_roles():
    """创建基础角色"""
    
    # 定义基础角色
    basic_roles = [
        {
            'name': '普通用户',
            'description': '普通用户，拥有基础查看和使用权限',
            'permissions': [
                'user.view_user',
                'bot.view_bot',
            ]
        },
        {
            'name': '内容管理员',
            'description': '内容管理员，可以管理内容和用户',
            'permissions': [
                'user.view_user',
                'user.view_role',
                'user.view_permission',
                'user.create_user',
                'user.update_user',
                'user.delete_user',
                'user.update_role',
                'user.create_role',
                'user.delete_role',
                'bot.view_bot',
                'bot.create_bot',
                'bot.update_bot',
                'bot.delete_bot',
            ]
        },
        {
            'name': '系统管理员',
            'description': '系统管理员，拥有完整权限',
            'permissions': [
                'user.view_user',
                'user.view_role',
                'user.view_permission',
                'user.create_user',
                'user.update_user',
                'user.delete_user',
                'user.create_role',
                'user.update_role',
                'user.delete_role',
                'user.create_permission',
                'user.update_permission',
                'user.delete_permission',
                'bot.view_bot',
                'bot.create_bot',
                'bot.update_bot',
                'bot.delete_bot',
                'audit.view_auditlog',
            ]
        }
    ]
    
    created_count = 0
    skipped_count = 0
    
    for role_config in basic_roles:
        # 检查角色是否已存在
        role = Role.objects.filter(name=role_config['name']).first()
        if role:
            # 如果被软删除，则恢复
            if role.deleted_at is not None:
                role.deleted_at = None
                role.save(update_fields=['deleted_at', 'updated_at'])
                print(f"✓ 恢复角色: {role_config['name']}")
            else:
                print(f"⊘ 角色已存在，跳过: {role_config['name']}")
                skipped_count += 1
                continue
        else:
            # 创建新角色
            role = Role.objects.create(
                name=role_config['name'],
                description=role_config['description']
            )
            print(f"✓ 创建角色: {role_config['name']}")
        
        # 关联权限
        permission_codes = role_config['permissions']
        permissions = Permission.objects.filter(code__in=permission_codes)
        
        if permissions.count() != len(permission_codes):
            # 检查哪些权限不存在
            existing_codes = set(permissions.values_list('code', flat=True))
            missing_codes = set(permission_codes) - existing_codes
            if missing_codes:
                print(f"  ⚠ 警告: 以下权限不存在，将被忽略: {missing_codes}")
        
        role.permissions.set(permissions)
        print(f"  └─ 已绑定 {permissions.count()} 个权限")
        created_count += 1
    
    print(f"\n总结: 创建 {created_count} 个角色, 跳过 {skipped_count} 个已存在的角色")

if __name__ == '__main__':
    init_basic_roles()
