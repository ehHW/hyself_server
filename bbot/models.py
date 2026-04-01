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
		db_table = "uploaded_file"
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
