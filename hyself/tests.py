import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from hyself.application.services.asset_references import create_chat_attachment_asset_reference, upsert_resource_center_reference
from hyself.asset_compat import create_user_profile_asset_reference, ensure_asset_reference_for_uploaded_file
from hyself.models import Asset, AssetReference, UploadedFile
from chat.models import ChatConversation, ChatConversationMember
from user.models import Permission, Role, User
from hyself.utils.upload import calc_file_md5, get_upload_root, get_user_relative_root, join_relative_path


@override_settings(MEDIA_URL="/media/")
class UploadRecycleRestoreTests(APITestCase):
	def setUp(self):
		super().setUp()
		self._temp_media_dir = tempfile.TemporaryDirectory()
		self.override = override_settings(MEDIA_ROOT=self._temp_media_dir.name)
		self.override.enable()
		self.user = User.objects.create_user(username="upload_tester", password="Test123456")
		self.file_role, _ = Role.objects.get_or_create(name="资源测试角色", defaults={"description": "资源/上传测试使用"})
		self.file_role.permissions.set(
			Permission.objects.filter(
				code__in=[
					"file.view_resource",
					"file.upload_file",
					"file.create_folder",
					"file.rename_resource",
					"file.delete_resource",
					"file.restore_resource",
					"file.save_chat_attachment",
				]
			)
		)
		self.user.roles.add(self.file_role)
		self.client.force_authenticate(self.user)

	def tearDown(self):
		self.override.disable()
		self._temp_media_dir.cleanup()
		super().tearDown()

	def _upload_small_file(self, name: str, content: bytes):
		return self._upload_small_file_to_parent(name, content)

	def _upload_small_file_to_parent(self, name: str, content: bytes, parent_id: int | None = None):
		payload = {"file": SimpleUploadedFile(name, content)}
		if parent_id is not None:
			payload["parent_id"] = str(parent_id)
		response = self.client.post(
			"/api/upload/small/",
			payload,
			format="multipart",
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return response.json()

	def _create_folder(self, folder_name: str):
		folder_relative_path = join_relative_path(get_user_relative_root(self.user), folder_name)
		(get_upload_root() / Path(folder_relative_path)).mkdir(parents=True, exist_ok=True)
		return UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=True,
			display_name=folder_name,
			stored_name=folder_name,
			relative_path=folder_relative_path,
			file_size=0,
			file_md5="",
		)

	def test_small_upload_restores_same_md5_from_recycle_bin(self):
		target_folder = self._create_folder("target-folder")
		original = self._upload_small_file("server_log.txt", b"same-file-content")
		original_id = original["file"]["id"]
		self.assertIsNotNone(original["file"].get("asset_reference_id"))

		delete_response = self.client.post("/api/upload/delete/", {"id": original_id}, format="json")
		self.assertEqual(delete_response.status_code, status.HTTP_200_OK)

		recycled = UploadedFile.objects.get(id=original_id)
		self.assertIsNotNone(recycled.recycled_at)

		restored = self._upload_small_file_to_parent("server_log.txt", b"same-file-content", parent_id=target_folder.id)
		self.assertEqual(restored["mode"], "instant")
		self.assertTrue(restored["restored_from_recycle"])
		self.assertEqual(restored["file"]["id"], original_id)
		self.assertEqual(restored["file"]["parent_id"], target_folder.id)
		self.assertTrue(restored["file"]["relative_path"].startswith(f"{target_folder.relative_path}/"))

		recycled.refresh_from_db()
		self.assertIsNone(recycled.recycled_at)
		self.assertEqual(recycled.parent_id, target_folder.id)
		self.assertEqual(UploadedFile.objects.filter(created_by=self.user, file_md5=recycled.file_md5, is_dir=False).count(), 1)
		self.assertTrue(AssetReference.objects.filter(legacy_uploaded_file_id=original_id).exists())

	def test_precheck_restores_same_md5_from_recycle_bin(self):
		target_folder = self._create_folder("precheck-target")
		upload_response = self._upload_small_file("archive.log", b"chunked-precheck-content")
		file_id = upload_response["file"]["id"]
		file_record = UploadedFile.objects.get(id=file_id)

		delete_response = self.client.post("/api/upload/delete/", {"id": file_id}, format="json")
		self.assertEqual(delete_response.status_code, status.HTTP_200_OK)

		file_path = get_upload_root() / Path(file_record.relative_path)
		file_md5 = calc_file_md5(file_path)
		precheck_response = self.client.post(
			"/api/upload/precheck/",
			{
				"file_md5": file_md5,
				"file_name": "archive.log",
				"file_size": len(b"chunked-precheck-content"),
				"parent_id": target_folder.id,
			},
			format="json",
		)

		self.assertEqual(precheck_response.status_code, status.HTTP_200_OK)
		body = precheck_response.json()
		self.assertTrue(body["exists"])
		self.assertTrue(body["restored_from_recycle"])
		self.assertEqual(body["file"]["id"], file_id)
		self.assertEqual(body["file"]["parent_id"], target_folder.id)
		self.assertTrue(body["file"]["relative_path"].startswith(f"{target_folder.relative_path}/"))
		self.assertIsNotNone(body["file"].get("asset_reference_id"))

		file_record.refresh_from_db()
		self.assertIsNone(file_record.recycled_at)
		self.assertEqual(file_record.parent_id, target_folder.id)

	def test_small_upload_instant_reuses_global_same_md5_file(self):
		other_user = User.objects.create_user(username="global_owner", password="Test123456")
		other_root = get_upload_root() / Path(get_user_relative_root(other_user))
		other_root.mkdir(parents=True, exist_ok=True)
		shared_path = other_root / "shared_manual.txt"
		shared_path.write_bytes(b"shared-global-content")
		source_entry = UploadedFile.objects.create(
			created_by=other_user,
			parent=None,
			is_dir=False,
			display_name="shared.txt",
			stored_name="shared_manual.txt",
			relative_path=join_relative_path(get_user_relative_root(other_user), "shared_manual.txt"),
			file_size=shared_path.stat().st_size,
			file_md5=calc_file_md5(shared_path),
		)

		response = self._upload_small_file("shared.txt", b"shared-global-content")

		self.assertEqual(response["mode"], "instant")
		self.assertFalse(response.get("restored_from_recycle", False))
		self.assertNotEqual(response["file"]["id"], source_entry.id)
		self.assertEqual(response["file"]["relative_path"], source_entry.relative_path)
		self.assertIsNotNone(response["file"].get("asset_reference_id"))
		self.assertTrue(
			UploadedFile.objects.filter(
				created_by=self.user,
				file_md5=source_entry.file_md5,
				relative_path=source_entry.relative_path,
				is_dir=False,
			).exists()
		)

	def test_user_scope_hides_chat_business_uploads(self):
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="chat-only.png",
			stored_name="chat-only.png",
			relative_path=join_relative_path(get_user_relative_root(self.user), "chat-only.png"),
			file_size=128,
			file_md5="1" * 32,
			business="chat",
		)
		ensure_asset_reference_for_uploaded_file(entry)

		response = self.client.get("/api/upload/files/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		items = response.json()["items"]
		self.assertFalse(any(item["display_name"] == "chat-only.png" for item in items))

	def test_system_scope_search_includes_chat_business_uploads(self):
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="chat-search.png",
			stored_name="chat-search.png",
			relative_path=join_relative_path(get_user_relative_root(self.user), "chat-search.png"),
			file_size=256,
			file_md5="2" * 32,
			business="chat",
		)
		ensure_asset_reference_for_uploaded_file(entry)
		admin_user = User.objects.create_superuser(username="asset_admin", password="Test123456")
		self.client.force_authenticate(admin_user)

		response = self.client.get(f"/api/upload/search/?scope=system&owner_user_id={self.user.id}&keyword=chat-search")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		items = response.json()["items"]
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["id"], entry.id)
		self.assertEqual(items[0]["resource_kind"], "chat_upload")

	def test_user_recycle_bin_delete_is_rejected(self):
		uploaded = self._upload_small_file("recycle-only.txt", b"recycle-content")
		entry_id = uploaded["file"]["id"]
		self.client.post("/api/upload/delete/", {"id": entry_id}, format="json")

		response = self.client.post("/api/upload/delete/", {"id": entry_id}, format="json")

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("已在回收站", response.json()["detail"])

	def test_delete_resource_entry_hides_from_user_scope_and_appears_in_recycle_bin(self):
		folder = self._create_folder("delete-folder")
		file_entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=folder,
			is_dir=False,
			display_name="keep.png",
			stored_name="keep.png",
			relative_path=join_relative_path(folder.relative_path, "keep.png"),
			file_size=123,
			file_md5="5" * 32,
		)
		ensure_asset_reference_for_uploaded_file(folder)
		ensure_asset_reference_for_uploaded_file(file_entry)

		response = self.client.post("/api/upload/delete/", {"id": folder.id}, format="json")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		root_listing = self.client.get("/api/upload/files/")
		self.assertEqual(root_listing.status_code, status.HTTP_200_OK)
		root_names = [item["display_name"] for item in root_listing.json()["items"]]
		self.assertNotIn("delete-folder", root_names)

		recycle_bin = UploadedFile.objects.get(created_by=self.user, business="recycle_bin")
		recycle_listing = self.client.get(f"/api/upload/files/?parent_id={recycle_bin.id}")
		self.assertEqual(recycle_listing.status_code, status.HTTP_200_OK)
		recycle_names = [item["display_name"] for item in recycle_listing.json()["items"]]
		self.assertIn("delete-folder", recycle_names)

		folder.refresh_from_db()
		self.assertIsNotNone(folder.recycled_at)
		folder_ref = ensure_asset_reference_for_uploaded_file(folder)
		self.assertEqual(folder_ref.status, AssetReference.Status.RECYCLED)

	def test_delete_saved_chat_attachment_appears_in_recycle_bin(self):
		sender = User.objects.create_user(username="image_sender", password="Test123456")
		conversation = ChatConversation.objects.create(type=ChatConversation.Type.DIRECT, status=ChatConversation.Status.ACTIVE)
		ChatConversationMember.objects.create(conversation=conversation, user=sender, status=ChatConversationMember.Status.ACTIVE)
		ChatConversationMember.objects.create(conversation=conversation, user=self.user, status=ChatConversationMember.Status.ACTIVE)
		asset = Asset.objects.create(
			storage_backend=Asset.StorageBackend.LOCAL,
			storage_key=join_relative_path(get_user_relative_root(sender), "shared-image.png"),
			mime_type="image/png",
			media_type=Asset.MediaType.IMAGE,
			file_size=512,
			file_md5="6" * 32,
			original_name="shared-image.png",
			extension=".png",
			created_by=sender,
		)
		chat_reference = AssetReference.objects.create(
			asset=asset,
			owner_user=sender,
			ref_domain=AssetReference.RefDomain.CHAT,
			ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
			ref_object_id=str(conversation.id),
			display_name="shared-image.png",
			relative_path_cache=asset.storage_key,
			status=AssetReference.Status.ACTIVE,
			visibility=AssetReference.Visibility.CONVERSATION,
		)

		save_response = self.client.post(
			"/api/upload/chat-attachments/save/",
			{"source_asset_reference_id": chat_reference.id},
			format="json",
		)
		self.assertEqual(save_response.status_code, status.HTTP_201_CREATED)
		saved_id = save_response.json()["file"]["id"]

		delete_response = self.client.post("/api/upload/delete/", {"id": saved_id}, format="json")
		self.assertEqual(delete_response.status_code, status.HTTP_200_OK)

		listing = self.client.get("/api/upload/files/")
		self.assertEqual(listing.status_code, status.HTTP_200_OK)
		self.assertFalse(any(item["id"] == saved_id for item in listing.json()["items"]))

		recycle_bin = UploadedFile.objects.get(created_by=self.user, business="recycle_bin")
		recycle_listing = self.client.get(f"/api/upload/files/?parent_id={recycle_bin.id}")
		self.assertEqual(recycle_listing.status_code, status.HTTP_200_OK)
		self.assertTrue(any(item["id"] == saved_id for item in recycle_listing.json()["items"]))

	def test_save_chat_attachment_restores_recycled_resource_entry(self):
		asset = Asset.objects.create(
			storage_backend=Asset.StorageBackend.LOCAL,
			storage_key=join_relative_path(get_user_relative_root(self.user), "restored-from-chat.mp4"),
			mime_type="video/mp4",
			media_type=Asset.MediaType.VIDEO,
			file_size=1024,
			original_name="restored-from-chat.mp4",
			extension=".mp4",
			created_by=self.user,
		)
		source_reference = AssetReference.objects.create(
			asset=asset,
			owner_user=self.user,
			ref_domain=AssetReference.RefDomain.CHAT,
			ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
			ref_object_id="conversation-1",
			display_name="restored-from-chat.mp4",
			relative_path_cache=asset.storage_key,
			status=AssetReference.Status.ACTIVE,
			visibility=AssetReference.Visibility.CONVERSATION,
		)
		recycled_entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="restored-from-chat.mp4",
			stored_name="restored-from-chat.mp4",
			relative_path=asset.storage_key,
			file_size=asset.file_size,
			file_md5="",
			recycled_at=timezone.now(),
		)

		response = self.client.post(
			"/api/upload/chat-attachments/save/",
			{"source_asset_reference_id": source_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		recycled_entry.refresh_from_db()
		self.assertIsNone(recycled_entry.recycled_at)
		self.assertEqual(recycled_entry.display_name, "restored-from-chat.mp4")
		self.assertEqual(
			UploadedFile.objects.filter(created_by=self.user, relative_path=asset.storage_key, is_dir=False).count(),
			1,
		)
		self.assertEqual(response.json()["file"]["id"], recycled_entry.id)

	def test_save_chat_attachment_allows_other_conversation_member(self):
		sender = User.objects.create_user(username="attachment_sender", password="Test123456")
		conversation = ChatConversation.objects.create(type=ChatConversation.Type.DIRECT, status=ChatConversation.Status.ACTIVE)
		ChatConversationMember.objects.create(conversation=conversation, user=sender, status=ChatConversationMember.Status.ACTIVE)
		ChatConversationMember.objects.create(conversation=conversation, user=self.user, status=ChatConversationMember.Status.ACTIVE)
		asset = Asset.objects.create(
			storage_backend=Asset.StorageBackend.LOCAL,
			storage_key=join_relative_path(get_user_relative_root(sender), "shared-video.mp4"),
			mime_type="video/mp4",
			media_type=Asset.MediaType.VIDEO,
			file_size=2048,
			file_md5="3" * 32,
			original_name="shared-video.mp4",
			extension=".mp4",
			created_by=sender,
		)
		chat_reference = AssetReference.objects.create(
			asset=asset,
			owner_user=sender,
			ref_domain=AssetReference.RefDomain.CHAT,
			ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
			ref_object_id=str(conversation.id),
			display_name="shared-video.mp4",
			relative_path_cache=asset.storage_key,
			status=AssetReference.Status.ACTIVE,
			visibility=AssetReference.Visibility.CONVERSATION,
		)

		response = self.client.post(
			"/api/upload/chat-attachments/save/",
			{"source_asset_reference_id": chat_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		entry = UploadedFile.objects.get(id=response.json()["file"]["id"])
		self.assertEqual(entry.created_by_id, self.user.id)
		self.assertEqual(entry.business, "")
		self.assertEqual(response.json()["file"]["resource_kind"], "resource_center")
		listing = self.client.get("/api/upload/files/")
		self.assertEqual(listing.status_code, status.HTTP_200_OK)
		file_items = [item for item in listing.json()["items"] if not item["is_dir"]]
		self.assertEqual(len(file_items), 1)

	def test_save_chat_attachment_converts_own_chat_upload_to_resource_center(self):
		conversation = ChatConversation.objects.create(type=ChatConversation.Type.DIRECT, status=ChatConversation.Status.ACTIVE)
		ChatConversationMember.objects.create(conversation=conversation, user=self.user, status=ChatConversationMember.Status.ACTIVE)
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="mine.mp4",
			stored_name="mine.mp4",
			relative_path=join_relative_path(get_user_relative_root(self.user), "mine.mp4"),
			file_size=4096,
			file_md5="4" * 32,
			business="chat",
		)
		source_reference = ensure_asset_reference_for_uploaded_file(entry)
		chat_reference = create_chat_attachment_asset_reference(source_reference=source_reference, owner_user=self.user, conversation_id=conversation.id)

		response = self.client.post(
			"/api/upload/chat-attachments/save/",
			{"source_asset_reference_id": chat_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		entry.refresh_from_db()
		self.assertEqual(entry.business, "")
		self.assertEqual(response.json()["file"]["id"], entry.id)
		self.assertEqual(response.json()["file"]["resource_kind"], "resource_center")
		listing = self.client.get("/api/upload/files/")
		self.assertEqual(listing.status_code, status.HTTP_200_OK)
		items = [item for item in listing.json()["items"] if not item["is_dir"]]
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["id"], entry.id)


@override_settings(MEDIA_URL="/media/")
class AssetCompatTests(APITestCase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="asset_user", password="Test123456")

	def test_ensure_asset_reference_for_regular_file(self):
		root = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=True,
			display_name="资料",
			stored_name="资料",
			relative_path=join_relative_path(get_user_relative_root(self.user), "资料"),
			file_size=0,
			file_md5="",
		)
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=root,
			is_dir=False,
			display_name="demo.png",
			stored_name="stored_demo.png",
			relative_path=join_relative_path(root.relative_path, "stored_demo.png"),
			file_size=123,
			file_md5="a" * 32,
		)

		reference = ensure_asset_reference_for_uploaded_file(entry)

		self.assertEqual(reference.ref_domain, AssetReference.RefDomain.RESOURCE_CENTER)
		self.assertEqual(reference.ref_type, AssetReference.RefType.FILE)
		self.assertEqual(reference.status, AssetReference.Status.ACTIVE)
		self.assertIsNotNone(reference.asset)
		self.assertEqual(reference.parent_reference.legacy_uploaded_file_id, root.id)
		self.assertEqual(reference.asset.media_type, Asset.MediaType.IMAGE)

	def test_ensure_asset_reference_for_recycled_directory(self):
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=True,
			display_name="回收目录",
			stored_name="回收目录",
			relative_path=join_relative_path(get_user_relative_root(self.user), "回收目录"),
			file_size=0,
			file_md5="",
			recycled_at=timezone.now(),
		)

		reference = ensure_asset_reference_for_uploaded_file(entry)

		self.assertIsNone(reference.asset)
		self.assertEqual(reference.ref_type, AssetReference.RefType.DIRECTORY)
		self.assertEqual(reference.status, AssetReference.Status.RECYCLED)

	def test_create_chat_attachment_asset_reference_uses_chat_domain_fields(self):
		asset = Asset.objects.create(
			storage_backend=Asset.StorageBackend.LOCAL,
			storage_key="users/asset_user/chat-diagram.png",
			mime_type="image/png",
			media_type=Asset.MediaType.IMAGE,
			file_size=2048,
			original_name="chat-diagram.png",
			extension=".png",
			created_by=self.user,
		)
		source_reference = AssetReference.objects.create(
			asset=asset,
			owner_user=self.user,
			ref_domain=AssetReference.RefDomain.RESOURCE_CENTER,
			ref_type=AssetReference.RefType.FILE,
			ref_object_id="uploaded-1",
			display_name="chat-diagram.png",
			relative_path_cache="users/asset_user/chat-diagram.png",
			status=AssetReference.Status.ACTIVE,
			visibility=AssetReference.Visibility.PRIVATE,
		)

		chat_reference = create_chat_attachment_asset_reference(
			source_reference=source_reference,
			owner_user=self.user,
			conversation_id=321,
		)

		self.assertEqual(chat_reference.asset_id, asset.id)
		self.assertEqual(chat_reference.ref_domain, AssetReference.RefDomain.CHAT)
		self.assertEqual(chat_reference.ref_type, AssetReference.RefType.CHAT_ATTACHMENT)
		self.assertEqual(chat_reference.ref_object_id, "321")
		self.assertEqual(chat_reference.visibility, AssetReference.Visibility.CONVERSATION)
		self.assertEqual(chat_reference.extra_metadata["source_asset_reference_id"], source_reference.id)
		self.assertEqual(chat_reference.extra_metadata["source_ref_domain"], AssetReference.RefDomain.RESOURCE_CENTER)

	def test_create_user_profile_asset_reference_reuses_same_reference(self):
		first_asset, first_reference = create_user_profile_asset_reference(
			user=self.user,
			display_name="avatar-a.png",
			relative_path="avatars/avatar-a.png",
			file_size=128,
			file_md5="1" * 32,
		)
		second_asset, second_reference = create_user_profile_asset_reference(
			user=self.user,
			display_name="avatar-b.png",
			relative_path="avatars/avatar-b.png",
			file_size=256,
			file_md5="2" * 32,
		)

		self.assertNotEqual(first_asset.id, second_asset.id)
		self.assertEqual(first_reference.id, second_reference.id)
		self.assertEqual(second_reference.asset_id, second_asset.id)
		self.assertEqual(second_reference.display_name, "avatar-b.png")
		self.assertEqual(second_reference.relative_path_cache, "avatars/avatar-b.png")
		self.assertEqual(
			AssetReference.objects.filter(
				owner_user=self.user,
				ref_domain=AssetReference.RefDomain.USER_PROFILE,
				ref_type=AssetReference.RefType.AVATAR,
				ref_object_id=str(self.user.id),
			).count(),
			1,
		)

	def test_upsert_resource_center_reference_reuses_same_legacy_entry_reference(self):
		entry = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="report.txt",
			stored_name="report.txt",
			relative_path=join_relative_path(get_user_relative_root(self.user), "report.txt"),
			file_size=30,
			file_md5="3" * 32,
		)
		asset = Asset.objects.create(
			storage_backend=Asset.StorageBackend.LOCAL,
			storage_key=entry.relative_path,
			mime_type="text/plain",
			media_type=Asset.MediaType.FILE,
			file_size=30,
			original_name="report.txt",
			extension=".txt",
			created_by=self.user,
		)

		first_reference = upsert_resource_center_reference(entry=entry, asset=asset, parent_reference=None)
		entry.display_name = "report-v2.txt"
		entry.relative_path = join_relative_path(get_user_relative_root(self.user), "report-v2.txt")
		entry.save(update_fields=["display_name", "relative_path", "updated_at"])
		second_reference = upsert_resource_center_reference(entry=entry, asset=asset, parent_reference=None)

		self.assertEqual(first_reference.id, second_reference.id)
		self.assertEqual(second_reference.display_name, "report-v2.txt")
		self.assertEqual(second_reference.relative_path_cache, entry.relative_path)
		self.assertEqual(second_reference.ref_domain, AssetReference.RefDomain.RESOURCE_CENTER)
