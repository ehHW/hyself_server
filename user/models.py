from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.utils import timezone

from utils.soft_delete import ActiveUserManager, AllUsersManager, SoftDeleteModel

SUPER_ADMIN_ROLE_NAME = "超级管理员"
SYSTEM_ADMIN_ROLE_NAME = "系统管理员"
DEFAULT_USER_ROLE_NAME = "普通用户"


class Permission(SoftDeleteModel):
	code = models.CharField(max_length=64, unique=True, verbose_name="权限编码")
	name = models.CharField(max_length=100, verbose_name="权限名称")
	description = models.CharField(max_length=255, blank=True, default="", verbose_name="描述")

	class Meta:
		db_table = "rbac_permission"
		ordering = ["id"]

	def __str__(self) -> str:
		return f"{self.name}({self.code})"


class Role(SoftDeleteModel):
	name = models.CharField(max_length=64, unique=True, verbose_name="角色名")
	description = models.CharField(max_length=255, blank=True, default="", verbose_name="描述")
	permissions = models.ManyToManyField(Permission, blank=True, related_name="roles", verbose_name="角色权限")

	class Meta:
		db_table = "rbac_role"
		ordering = ["id"]

	def __str__(self) -> str:
		return self.name

	def is_super_admin_role(self) -> bool:
		return self.name == SUPER_ADMIN_ROLE_NAME


class User(SoftDeleteModel, AbstractUser):
	username_validator = UnicodeUsernameValidator()
	username = models.CharField(
		"username",
		max_length=255,
		unique=True,
		help_text="Required. Letters, digits and @/./+/-/_ only.",
		validators=[username_validator],
		error_messages={"unique": "A user with that username already exists."},
	)
	display_name = models.TextField(blank=True, default="", verbose_name="显示名")
	avatar = models.CharField(max_length=500, blank=True, default="", verbose_name="头像")
	phone_number = models.CharField(max_length=30, blank=True, default="", verbose_name="电话号码")
	roles = models.ManyToManyField(Role, blank=True, related_name="users", verbose_name="用户角色")

	objects = ActiveUserManager()
	all_objects = AllUsersManager()

	class Meta:
		db_table = "rbac_user"
		ordering = ["id"]

	def has_permission_code(self, code: str) -> bool:
		if self.is_superuser:
			return True
		if not self.is_authenticated:
			return False
		return self.roles.filter(permissions__code=code).exists()

	def has_super_admin_role(self) -> bool:
		if self.is_superuser:
			return True
		return self.roles.filter(name=SUPER_ADMIN_ROLE_NAME).exists()

	def delete(self, using=None, keep_parents=False):
		if self.deleted_at is None:
			self.deleted_at = timezone.now()
			self.is_active = False
			self.save(update_fields=["deleted_at", "updated_at", "is_active"])


class AuditLog(SoftDeleteModel):
	ACTION_CHOICES = [
		("login", "登录"),
		("create", "创建"),
		("update", "更新"),
		("delete", "删除"),
		("kickout", "踢下线"),
		("protect_block", "保护拦截"),
	]
	STATUS_CHOICES = [
		("success", "成功"),
		("failed", "失败"),
		("blocked", "阻止"),
	]

	actor = models.ForeignKey(
		"user.User",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="audit_logs",
		verbose_name="操作人",
	)
	action = models.CharField(max_length=32, choices=ACTION_CHOICES, verbose_name="动作")
	target_type = models.CharField(max_length=64, blank=True, default="", verbose_name="目标类型")
	target_id = models.CharField(max_length=64, blank=True, default="", verbose_name="目标ID")
	target_repr = models.CharField(max_length=255, blank=True, default="", verbose_name="目标摘要")
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="success", verbose_name="结果")
	detail = models.CharField(max_length=500, blank=True, default="", verbose_name="详细信息")
	metadata = models.JSONField(blank=True, default=dict, verbose_name="附加数据")
	ip_address = models.CharField(max_length=64, blank=True, default="", verbose_name="IP地址")
	user_agent = models.CharField(max_length=500, blank=True, default="", verbose_name="User-Agent")

	class Meta:
		db_table = "audit_log"
		ordering = ["-id"]

	def __str__(self) -> str:
		actor_name = self.actor.username if self.actor else "anonymous"
		return f"{actor_name}:{self.action}:{self.target_type}:{self.status}"


class UserPreference(models.Model):
	theme_mode = models.CharField(max_length=20, default="light", verbose_name="主题模式")
	chat_receive_notification = models.BooleanField(default=True, verbose_name="接收聊天通知")
	chat_list_sort_mode = models.CharField(max_length=20, default="recent", verbose_name="聊天列表排序模式")
	chat_stealth_inspect_enabled = models.BooleanField(default=False, verbose_name="超级管理员隐身巡检开关")
	settings_json = models.JSONField(blank=True, default=dict, verbose_name="扩展设置")
	created_at = models.DateTimeField(default=timezone.now, editable=False)
	updated_at = models.DateTimeField(auto_now=True)
	user = models.OneToOneField(
		"user.User",
		on_delete=models.CASCADE,
		related_name="preference",
		verbose_name="用户",
	)

	class Meta:
		db_table = "user_preference"
		ordering = ["id"]
		indexes = [
			models.Index(fields=["chat_stealth_inspect_enabled"]),
		]

	def __str__(self) -> str:
		return f"Preference<{self.user_id}>"
