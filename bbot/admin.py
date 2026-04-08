from django.contrib import admin

from bbot.models import Asset, AssetReference, UploadedFile


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
	list_display = ("id", "display_name", "is_dir", "parent_id", "created_by", "file_size", "deleted_at")
	list_filter = ("is_dir", "created_by", "deleted_at")
	search_fields = ("display_name", "stored_name", "relative_path", "file_md5")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
	list_display = ("id", "original_name", "media_type", "storage_backend", "storage_key", "file_size", "created_by", "deleted_at")
	list_filter = ("media_type", "storage_backend", "deleted_at")
	search_fields = ("original_name", "storage_key", "file_md5", "sha256")


@admin.register(AssetReference)
class AssetReferenceAdmin(admin.ModelAdmin):
	list_display = ("id", "display_name", "ref_domain", "ref_type", "status", "owner_user", "asset_id", "legacy_uploaded_file_id")
	list_filter = ("ref_domain", "ref_type", "status", "visibility", "deleted_at")
	search_fields = ("display_name", "ref_object_id", "relative_path_cache")
