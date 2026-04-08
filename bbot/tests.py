import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from bbot.asset_compat import ensure_asset_reference_for_uploaded_file
from bbot.models import Asset, AssetReference, UploadedFile
from user.models import User
from utils.upload import calc_file_md5, get_upload_root, get_user_relative_root, join_relative_path


@override_settings(MEDIA_URL="/media/")
class UploadRecycleRestoreTests(APITestCase):
	def setUp(self):
		super().setUp()
		self._temp_media_dir = tempfile.TemporaryDirectory()
		self.override = override_settings(MEDIA_ROOT=self._temp_media_dir.name)
		self.override.enable()
		self.user = User.objects.create_user(username="upload_tester", password="Test123456")
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
