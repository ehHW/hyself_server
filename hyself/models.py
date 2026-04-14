from django.db import models
from django.conf import settings
from django.utils import timezone

from utils.soft_delete import SoftDeleteModel


class UploadedFile(SoftDeleteModel):
	business = models.CharField(max_length=64, blank=True, default="", verbose_name="业务分类(兼容字段)")
	is_system = models.BooleanField(default=False, db_index=True, verbose_name="是否系统内置")
	recycled_at = models.DateTimeField(null=True, blank=True, default=None, db_index=True, verbose_name="移入回收站时间")
	recycle_original_parent = models.ForeignKey(
		"self",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="recycle_restorables",
		verbose_name="回收站原父目录",
	)
	stored_name = models.CharField(max_length=255, blank=True, default="", verbose_name="存储文件名")
	display_name = models.CharField(max_length=255, blank=True, default="", verbose_name="展示文件名")
	file_md5 = models.CharField(max_length=32, blank=True, default="", db_index=True, verbose_name="文件MD5")
	file_size = models.BigIntegerField(default=0, verbose_name="文件大小")
	relative_path = models.CharField(max_length=500, blank=True, default="", verbose_name="上传相对路径")
	is_dir = models.BooleanField(default=False, verbose_name="是否目录")
	parent = models.ForeignKey(
		"self",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="children",
		verbose_name="父目录",
	)
	created_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="uploaded_files",
		verbose_name="上传用户",
	)
	class Meta:
		db_table = "hyself_uploaded_file"
		ordering = ["-id"]
		indexes = [
			models.Index(fields=["created_by", "parent"]),
			models.Index(fields=["created_by", "is_dir"]),
		]

	def __str__(self) -> str:
		return f"{self.display_name}({self.file_md5})"

	def delete(self, using=None, keep_parents=False):
		if self.deleted_at is not None:
			return

		now = timezone.now()
		ids = [self.id]
		cursor = [self.id]
		while cursor:
			child_ids = list(
				UploadedFile.all_objects.filter(parent_id__in=cursor, deleted_at__isnull=True).values_list("id", flat=True)
			)
			if not child_ids:
				break
			ids.extend(child_ids)
			cursor = child_ids

		UploadedFile.all_objects.filter(id__in=ids, deleted_at__isnull=True).update(deleted_at=now, updated_at=now)


class Asset(SoftDeleteModel):
	class StorageBackend(models.TextChoices):
		LOCAL = "local", "本地存储"

	class MediaType(models.TextChoices):
		FILE = "file", "文件"
		IMAGE = "image", "图片"
		AUDIO = "audio", "音频"
		VIDEO = "video", "视频"
		AVATAR = "avatar", "头像"
		SYSTEM = "system", "系统"

	file_md5 = models.CharField(max_length=32, null=True, blank=True, db_index=True, verbose_name="文件MD5")
	sha256 = models.CharField(max_length=64, null=True, blank=True, db_index=True, verbose_name="文件SHA256")
	storage_backend = models.CharField(max_length=32, choices=StorageBackend.choices, default=StorageBackend.LOCAL, db_index=True, verbose_name="存储后端")
	storage_key = models.CharField(max_length=500, db_index=True, verbose_name="存储键")
	mime_type = models.CharField(max_length=255, blank=True, default="", verbose_name="MIME 类型")
	media_type = models.CharField(max_length=32, choices=MediaType.choices, default=MediaType.FILE, db_index=True, verbose_name="媒体类型")
	file_size = models.BigIntegerField(default=0, verbose_name="文件大小")
	original_name = models.CharField(max_length=255, blank=True, default="", verbose_name="原始文件名")
	extension = models.CharField(max_length=32, blank=True, default="", verbose_name="扩展名")
	width = models.PositiveIntegerField(null=True, blank=True, verbose_name="宽度")
	height = models.PositiveIntegerField(null=True, blank=True, verbose_name="高度")
	duration_seconds = models.FloatField(null=True, blank=True, verbose_name="时长(秒)")
	created_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="created_assets",
		verbose_name="创建人",
	)
	extra_metadata = models.JSONField(default=dict, blank=True, verbose_name="扩展元数据")

	class Meta:
		db_table = "hyself_asset"
		ordering = ["-id"]
		constraints = [
			models.UniqueConstraint(fields=["storage_backend", "storage_key"], name="uq_asset_storage_backend_key"),
		]
		indexes = [
			models.Index(fields=["media_type", "created_by"]),
		]

	def __str__(self) -> str:
		return f"{self.original_name or self.storage_key}({self.media_type})"


class AssetReference(SoftDeleteModel):
	class RefDomain(models.TextChoices):
		RESOURCE_CENTER = "resource_center", "资源中心"
		CHAT = "chat", "聊天"
		USER_PROFILE = "user_profile", "用户资料"
		SYSTEM = "system", "系统"

	class RefType(models.TextChoices):
		FILE = "file", "文件"
		DIRECTORY = "directory", "目录"
		AVATAR = "avatar", "头像"
		CHAT_ATTACHMENT = "chat_attachment", "聊天附件"

	class Status(models.TextChoices):
		ACTIVE = "active", "正常"
		RECYCLED = "recycled", "回收站"
		DELETED = "deleted", "删除"

	class Visibility(models.TextChoices):
		PRIVATE = "private", "私有"
		CONVERSATION = "conversation", "会话内"
		PUBLIC = "public", "公开"
		SYSTEM = "system", "系统"

	asset = models.ForeignKey(
		Asset,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="references",
		verbose_name="资产",
	)
	owner_user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="owned_asset_references",
		verbose_name="引用归属用户",
	)
	ref_domain = models.CharField(max_length=32, choices=RefDomain.choices, default=RefDomain.RESOURCE_CENTER, db_index=True, verbose_name="引用域")
	ref_type = models.CharField(max_length=32, choices=RefType.choices, default=RefType.FILE, db_index=True, verbose_name="引用类型")
	ref_object_id = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name="业务对象ID")
	display_name = models.CharField(max_length=255, blank=True, default="", verbose_name="显示名")
	parent_reference = models.ForeignKey(
		"self",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="children",
		verbose_name="父引用",
	)
	relative_path_cache = models.CharField(max_length=500, blank=True, default="", verbose_name="相对路径缓存")
	status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE, db_index=True, verbose_name="状态")
	recycled_at = models.DateTimeField(null=True, blank=True, default=None, db_index=True, verbose_name="移入回收站时间")
	visibility = models.CharField(max_length=32, choices=Visibility.choices, default=Visibility.PRIVATE, db_index=True, verbose_name="可见性")
	extra_metadata = models.JSONField(default=dict, blank=True, verbose_name="扩展元数据")
	legacy_uploaded_file = models.OneToOneField(
		UploadedFile,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="asset_reference_compat",
		verbose_name="兼容 UploadedFile",
	)

	class Meta:
		db_table = "hyself_asset_reference"
		ordering = ["-id"]
		indexes = [
			models.Index(fields=["owner_user", "parent_reference", "status"]),
			models.Index(fields=["ref_domain", "ref_type", "ref_object_id"]),
			models.Index(fields=["asset", "status"]),
		]

	def __str__(self) -> str:
		return f"{self.display_name or self.ref_object_id}({self.ref_domain}:{self.ref_type})"
