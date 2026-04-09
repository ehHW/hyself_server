from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from user.models import Permission, Role, User


class UserDeleteProtectionTests(APITestCase):
	def setUp(self):
		self.delete_permission, _ = Permission.objects.get_or_create(code="user.delete_user", defaults={"name": "删除用户"})
		self.role, _ = Role.objects.get_or_create(name="测试管理员", defaults={"description": "用于测试删除用户权限"})
		self.role.permissions.clear()
		self.role.permissions.add(self.delete_permission)

		self.operator = User.objects.create_user(username="operator", password="123456")
		self.operator.roles.add(self.role)

		self.target_user = User.objects.create_user(username="target_user", password="123456")

	def test_user_cannot_delete_self(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("users-detail", kwargs={"pk": self.operator.id})

		response = self.client.delete(url)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data.get("detail"), "不能删除当前登录用户")
		self.assertIsNotNone(User.objects.filter(id=self.operator.id).first())

	def test_user_can_delete_other_user_with_permission(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("users-detail", kwargs={"pk": self.target_user.id})

		response = self.client.delete(url)

		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
		deleted_target = User.all_objects.filter(id=self.target_user.id).first()
		self.assertIsNotNone(deleted_target)
		self.assertFalse(deleted_target.is_active)
		self.assertIsNotNone(deleted_target.deleted_at)


class UserSelfProtectionTests(APITestCase):
	def setUp(self):
		self.update_permission, _ = Permission.objects.get_or_create(code="user.update_user", defaults={"name": "修改用户"})
		self.role, _ = Role.objects.get_or_create(name="测试用户管理员", defaults={"description": "用于测试用户更新权限"})
		self.role.permissions.clear()
		self.role.permissions.add(self.update_permission)

		self.operator = User.objects.create_user(username="self_operator", password="123456", is_active=True)
		self.operator.roles.add(self.role)

	def test_user_cannot_disable_self(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("users-detail", kwargs={"pk": self.operator.id})

		response = self.client.patch(url, {"is_active": False}, format="json")

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data.get("detail"), "不能停用当前登录用户")
		self.operator.refresh_from_db()
		self.assertTrue(self.operator.is_active)

	def test_user_cannot_change_self_roles(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("users-detail", kwargs={"pk": self.operator.id})

		response = self.client.patch(url, {"role_ids": []}, format="json")

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data.get("detail"), "不能修改当前登录用户角色")


class RoleDeleteProtectionTests(APITestCase):
	def setUp(self):
		self.delete_role_permission, _ = Permission.objects.get_or_create(code="user.delete_role", defaults={"name": "删除角色"})
		self.role_in_use, _ = Role.objects.get_or_create(name="测试在用角色", defaults={"description": "用于测试在用角色删除保护"})
		self.role_in_use.permissions.add(self.delete_role_permission)

		self.operator = User.objects.create_user(username="role_operator", password="123456")
		self.operator.roles.add(self.role_in_use)

	def test_user_cannot_delete_own_in_use_role(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("roles-detail", kwargs={"pk": self.role_in_use.id})

		response = self.client.delete(url)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data.get("detail"), "不能删除当前用户正在使用的角色")
		self.assertIsNotNone(Role.objects.filter(id=self.role_in_use.id).first())


class ChangePasswordTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="profile_user", password="OldPass123!", email="profile@example.com")
		self.url = reverse("change_password")

	def test_user_can_change_password(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.post(
			self.url,
			{
				"current_password": "OldPass123!",
				"new_password": "NewPass456!",
				"confirm_password": "NewPass456!",
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data.get("detail"), "密码修改成功")
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password("NewPass456!"))

	def test_change_password_rejects_wrong_current_password(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.post(
			self.url,
			{
				"current_password": "WrongPass123!",
				"new_password": "NewPass456!",
				"confirm_password": "NewPass456!",
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data.get("current_password"), ["当前密码错误"])
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password("OldPass123!"))


class CorePermissionOperationTests(APITestCase):
	def setUp(self):
		self.create_permission_perm, _ = Permission.objects.get_or_create(code="user.create_permission", defaults={"name": "创建权限"})
		self.update_permission_perm, _ = Permission.objects.get_or_create(code="user.update_permission", defaults={"name": "修改权限"})
		self.delete_permission_perm, _ = Permission.objects.get_or_create(code="user.delete_permission", defaults={"name": "删除权限"})

		self.admin_role, _ = Role.objects.get_or_create(name="测试权限管理员", defaults={"description": "用于测试权限管理"})
		self.admin_role.permissions.clear()
		self.admin_role.permissions.add(
			self.create_permission_perm,
			self.update_permission_perm,
			self.delete_permission_perm,
		)

		self.operator = User.objects.create_user(username="perm_operator", password="123456")
		self.operator.roles.add(self.admin_role)

		self.core_permission, _ = Permission.objects.get_or_create(code="user.core_demo", defaults={"name": "核心演示权限"})

	def test_non_superuser_cannot_create_core_permission(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("permissions-list")

		response = self.client.post(
			url,
			{"code": "user.created_by_operator", "name": "非超管尝试创建核心权限", "description": "blocked"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		self.assertEqual(response.data.get("detail"), "核心权限仅超级管理员可操作")

	def test_non_superuser_cannot_update_core_permission(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("permissions-detail", kwargs={"pk": self.core_permission.id})

		response = self.client.patch(url, {"name": "attempt update"}, format="json")

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		self.assertEqual(response.data.get("detail"), "核心权限仅超级管理员可操作")

	def test_non_superuser_cannot_delete_core_permission(self):
		self.client.force_authenticate(user=self.operator)
		url = reverse("permissions-detail", kwargs={"pk": self.core_permission.id})

		response = self.client.delete(url)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		self.assertEqual(response.data.get("detail"), "核心权限仅超级管理员可操作")
