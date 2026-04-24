from django.apps import apps
from django.db.models.signals import post_save
from django.db.models.signals import post_migrate
from django.dispatch import receiver

DEFAULT_PERMISSIONS = [
    ("user.view_user", "查看用户"),
    ("user.create_user", "创建用户"),
    ("user.update_user", "修改用户"),
    ("user.delete_user", "删除用户"),
    ("user.view_role", "查看角色"),
    ("user.create_role", "创建角色"),
    ("user.update_role", "修改角色"),
    ("user.delete_role", "删除角色"),
    ("user.view_permission", "查看权限"),
    ("user.create_permission", "创建权限"),
    ("user.update_permission", "修改权限"),
    ("user.delete_permission", "删除权限"),
    ("file.view_resource", "查看资源中心"),
    ("file.upload_file", "上传资源文件"),
    ("file.create_folder", "新建资源目录"),
    ("file.rename_resource", "重命名资源"),
    ("file.delete_resource", "删除资源"),
    ("file.restore_resource", "还原资源"),
    ("file.save_chat_attachment", "保存聊天附件到资源中心"),
    ("file.manage_system_resource", "管理系统资源"),
    ("chat.view_conversation", "查看会话"),
    ("chat.send_message", "发送消息"),
    ("chat.send_attachment", "发送附件"),
    ("chat.create_group", "创建群聊"),
    ("chat.manage_group", "群内管理能力"),
    ("chat.add_friend", "好友关系管理"),
    ("chat.delete_friend", "删除好友"),
    ("chat.hide_conversation", "隐藏会话列表项"),
    ("chat.pin_conversation", "置顶会话"),
    ("chat.forward_message", "转发消息"),
    ("chat.revoke_message", "撤回自己发送的消息"),
    ("chat.delete_message", "删除消息显示记录"),
    ("chat.restore_revoked_message", "撤回后恢复到输入框"),
    ("chat.review_all_messages", "巡检查看全部聊天记录"),
    ("system.publish_announcement", "发布系统公告"),
    ("game.view_leaderboard", "查看游戏排行榜"),
    ("game.submit_best_record", "提交游戏成绩"),
    ("entertainment.view_music", "查看音乐中心"),
    ("entertainment.view_video", "查看视频中心"),
]

DEFAULT_PERMISSION_DESCRIPTIONS = {
    "chat.create_group": "允许创建新的群聊会话，不包含群内成员管理能力。",
    "chat.manage_group": "允许使用群内管理入口，但实际能否审批、禁言、移除成员，还取决于当前用户在对应群里是否是群主或管理员。",
    "chat.add_friend": "允许发起和处理好友申请，以及维护好友相关设置。",
    "chat.hide_conversation": "允许把会话从当前列表中隐藏，不会删除真实消息记录。",
    "chat.revoke_message": "允许撤回自己发送且仍在可撤回时限内的消息。",
    "chat.delete_message": "允许删除当前界面的消息显示记录，不等同于全局清除服务端所有消息。",
    "chat.restore_revoked_message": "允许在撤回消息后，把原内容恢复到输入框中继续编辑并重新发送。",
    "chat.review_all_messages": "允许进入巡检能力并查看全部聊天记录，属于审计型权限。",
}

DEFAULT_ROLE_BASELINE_PERMISSION_CODES = [
    "file.view_resource",
    "file.upload_file",
    "file.create_folder",
    "file.rename_resource",
    "file.delete_resource",
    "file.restore_resource",
    "file.save_chat_attachment",
    "chat.view_conversation",
    "chat.send_message",
    "chat.send_attachment",
    "chat.add_friend",
    "chat.delete_friend",
    "chat.hide_conversation",
    "chat.pin_conversation",
    "chat.forward_message",
    "chat.revoke_message",
    "chat.delete_message",
    "chat.restore_revoked_message",
    "game.view_leaderboard",
    "game.submit_best_record",
    "entertainment.view_music",
    "entertainment.view_video",
]

SUPER_ADMIN_ROLE_NAME = "超级管理员"
SYSTEM_ADMIN_ROLE_NAME = "系统管理员"
DEFAULT_USER_ROLE_NAME = "普通用户"
SUPER_ADMIN_ONLY_PERMISSION_CODES = {"chat.review_all_messages"}

FORMAL_ROLE_PERMISSION_CODES = {
    SYSTEM_ADMIN_ROLE_NAME: [
        code
        for code, _ in DEFAULT_PERMISSIONS
        if code not in SUPER_ADMIN_ONLY_PERMISSION_CODES
    ],
}

FORMAL_ROLE_DESCRIPTIONS = {
    SYSTEM_ADMIN_ROLE_NAME: "系统管理员，拥有除超级管理员专属能力外的后台与业务权限",
}


def ensure_default_permissions_synced() -> None:
    Permission = apps.get_model("user", "Permission")
    Role = apps.get_model("user", "Role")
    User = apps.get_model("user", "User")

    for code, name in DEFAULT_PERMISSIONS:
        permission = Permission.all_objects.filter(code=code).first()
        if permission is None:
            Permission.all_objects.create(code=code, name=name)
        else:
            updates = []
            if permission.name != name:
                permission.name = name
                updates.append("name")
            next_description = DEFAULT_PERMISSION_DESCRIPTIONS.get(code, "")
            if permission.description != next_description:
                permission.description = next_description
                updates.append("description")
            if permission.deleted_at is not None:
                permission.deleted_at = None
                updates.append("deleted_at")
            if updates:
                updates.append("updated_at")
                permission.save(update_fields=updates)

    super_admin_role = Role.all_objects.filter(name=SUPER_ADMIN_ROLE_NAME).first()
    if super_admin_role is None:
        super_admin_role = Role.all_objects.create(
            name=SUPER_ADMIN_ROLE_NAME,
            description="系统内置超级管理员角色，默认拥有全部权限",
        )
    elif super_admin_role.deleted_at is not None:
        super_admin_role.deleted_at = None
        super_admin_role.save(update_fields=["deleted_at", "updated_at"])
    super_admin_role.permissions.set(Permission.objects.all())

    default_user_role = Role.all_objects.filter(name=DEFAULT_USER_ROLE_NAME).first()
    if default_user_role is None:
        default_user_role = Role.all_objects.create(
            name=DEFAULT_USER_ROLE_NAME,
            description="系统默认基础角色，确保用户至少具备一个角色归属",
        )
        default_user_role.permissions.set(
            Permission.objects.filter(
                code__in=DEFAULT_ROLE_BASELINE_PERMISSION_CODES,
            ),
        )
    elif default_user_role.deleted_at is not None:
        default_user_role.deleted_at = None
        default_user_role.save(update_fields=["deleted_at", "updated_at"])

    for role_name, permission_codes in FORMAL_ROLE_PERMISSION_CODES.items():
        role = Role.all_objects.filter(name=role_name).first()
        if role is None:
            role = Role.all_objects.create(
                name=role_name,
                description=FORMAL_ROLE_DESCRIPTIONS.get(role_name, ""),
            )
        else:
            updates = []
            next_description = FORMAL_ROLE_DESCRIPTIONS.get(role_name, "")
            if role.description != next_description:
                role.description = next_description
                updates.append("description")
            if role.deleted_at is not None:
                role.deleted_at = None
                updates.append("deleted_at")
            if updates:
                updates.append("updated_at")
                role.save(update_fields=updates)
        role.permissions.set(
            Permission.objects.filter(code__in=permission_codes),
        )

    for user in User.objects.filter(is_superuser=True):
        user.roles.add(super_admin_role)


@receiver(post_migrate)
def bootstrap_default_permissions(sender, **kwargs):
    if getattr(sender, "name", "") != "user":
        return
    ensure_default_permissions_synced()


@receiver(post_save)
def bind_super_admin_role(sender, instance, created, **kwargs):
    sender_meta = getattr(sender, "_meta", None)
    if not sender_meta:
        return
    if sender_meta.app_label != "user" or sender_meta.model_name != "user":
        return
    if not getattr(instance, "is_superuser", False):
        return

    Role = apps.get_model("user", "Role")
    Permission = apps.get_model("user", "Permission")
    role = Role.all_objects.filter(name=SUPER_ADMIN_ROLE_NAME).first()
    if role is None:
        role = Role.all_objects.create(
            name=SUPER_ADMIN_ROLE_NAME,
            description="系统内置超级管理员角色，默认拥有全部权限",
        )
    elif role.deleted_at is not None:
        role.deleted_at = None
        role.save(update_fields=["deleted_at", "updated_at"])
    if role.permissions.count() != Permission.objects.count():
        role.permissions.set(Permission.objects.all())
    instance.roles.add(role)


@receiver(post_save)
def bind_new_permission_to_super_admin(sender, instance, created, **kwargs):
    sender_meta = getattr(sender, "_meta", None)
    if not sender_meta:
        return
    if sender_meta.app_label != "user" or sender_meta.model_name != "permission":
        return
    if not created:
        return

    Role = apps.get_model("user", "Role")
    role = Role.all_objects.filter(name=SUPER_ADMIN_ROLE_NAME).first()
    if role is None:
        role = Role.all_objects.create(
            name=SUPER_ADMIN_ROLE_NAME,
            description="系统内置超级管理员角色，默认拥有全部权限",
        )
    elif role.deleted_at is not None:
        role.deleted_at = None
        role.save(update_fields=["deleted_at", "updated_at"])
    role.permissions.add(instance)
