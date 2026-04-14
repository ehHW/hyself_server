from auth.permissions import AuthenticatedPermission as IsAuthenticated, raise_permission_denied
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView
from django.db.models import Count, Q

from user.access_context import build_permission_context_payload, ensure_default_user_role, ensure_user_has_minimum_role
from user.models import Permission, Role, User, SUPER_ADMIN_ROLE_NAME
from user.auth.permissions import ActionPermission
from user.signals import ensure_default_permissions_synced
from user.serializers import (
	ChangePasswordSerializer,
	LoginSerializer,
	PermissionSerializer,
	ProfileSerializer,
	ProfileUpdateSerializer,
	RoleSerializer,
	UserSerializer,
	SUPER_ADMIN_ONLY_PERMISSION_CODES,
)
from utils.audit import write_audit_log
from ws.events import notify_user_force_logout, notify_user_permission_updated


CORE_PERMISSION_CODE_PREFIXES = ("user.", "audit.")


def _as_bool(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in {"1", "true", "yes", "on"}
	if isinstance(value, (int, float)):
		return bool(value)
	return False


def _extract_role_ids(raw_role_ids):
	if raw_role_ids is None:
		return None
	if isinstance(raw_role_ids, str):
		raw = raw_role_ids.strip()
		if not raw:
			return []
		parts = [item.strip() for item in raw.split(",") if item.strip()]
		result = []
		for part in parts:
			try:
				result.append(int(part))
			except (TypeError, ValueError):
				continue
		return result
	if isinstance(raw_role_ids, (list, tuple, set)):
		result = []
		for item in raw_role_ids:
			try:
				result.append(int(item))
			except (TypeError, ValueError):
				continue
		return result
	return []


def _is_core_permission_code(code: str) -> bool:
	permission_code = str(code or "").strip()
	if not permission_code:
		return False
	return permission_code.startswith(CORE_PERMISSION_CODE_PREFIXES)


def ensure_super_admin_role() -> None:
	"""确保系统存在超级管理员角色并绑定所有超管用户。"""

	ensure_default_permissions_synced()
	role = Role.all_objects.filter(name=SUPER_ADMIN_ROLE_NAME).first()
	if role is None:
		role = Role.all_objects.create(
			name=SUPER_ADMIN_ROLE_NAME,
			description="系统内置超级管理员角色，默认拥有全部权限",
		)
	elif role.deleted_at is not None:
		role.deleted_at = None
		role.save(update_fields=["deleted_at", "updated_at"])
	all_permissions = Permission.objects.all()
	if role.permissions.count() != all_permissions.count():
		role.permissions.set(all_permissions)

	for user in User.objects.filter(is_superuser=True):
		user.roles.add(role)


def ensure_default_role() -> None:
	ensure_default_user_role()


def notify_users_permission_context_changed(user_ids: list[int] | set[int], *, reason: str) -> None:
	for user_id in {int(item) for item in user_ids if item}:
		notify_user_permission_updated(user_id, reason=reason)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
	ensure_super_admin_role()
	ensure_default_role()
	serializer = LoginSerializer(data=request.data)
	try:
		serializer.is_valid(raise_exception=True)
	except Exception:
		write_audit_log(
			request,
			"login",
			"failed",
			detail="登录失败",
			metadata={"username": str(request.data.get("username", ""))},
		)
		raise
	user = serializer.validated_data["user"]
	ensure_user_has_minimum_role(user)
	write_audit_log(request, "login", "success", detail="登录成功", target=user)

	refresh = RefreshToken.for_user(user)
	return Response(
		{
			"access": str(refresh.access_token),
			"refresh": str(refresh),
			"user": ProfileSerializer(user).data,
		}
	)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def profile_view(request):
	ensure_super_admin_role()
	ensure_default_role()
	ensure_user_has_minimum_role(request.user)
	if request.method == "PATCH":
		serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
		serializer.is_valid(raise_exception=True)
		serializer.save()
		write_audit_log(request, "update", "success", detail="更新个人资料成功", target=request.user)
		return Response(ProfileSerializer(request.user).data)
	return Response(ProfileSerializer(request.user).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def permission_context_view(request):
	ensure_super_admin_role()
	ensure_default_role()
	ensure_user_has_minimum_role(request.user)
	return Response(build_permission_context_payload(request.user))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password_view(request):
	ensure_super_admin_role()
	serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
	serializer.is_valid(raise_exception=True)
	serializer.save()
	write_audit_log(request, "update_password", "success", detail="修改密码成功", target=request.user)
	return Response({"detail": "密码修改成功"})


class UserViewSet(viewsets.ModelViewSet):
	queryset = User.objects.prefetch_related("roles__permissions").all().order_by("-id")
	serializer_class = UserSerializer
	permission_classes = [IsAuthenticated, ActionPermission]
	required_permission_map = {
		"list": "user.view_user",
		"retrieve": "user.view_user",
		"create": "user.create_user",
		"update": "user.update_user",
		"partial_update": "user.update_user",
		"destroy": "user.delete_user",
		"kickout": "user.update_user",
	}

	def create(self, request, *args, **kwargs):
		if not request.user.is_superuser:
			write_audit_log(request, "create", "blocked", detail="非超级管理员尝试创建用户")
			raise_permission_denied()
		response = super().create(request, *args, **kwargs)
		target = User.objects.filter(pk=response.data.get("id")).first() if isinstance(response.data, dict) else None
		if target is not None:
			notify_users_permission_context_changed([target.id], reason="user_created")
		write_audit_log(request, "create", "success", detail="创建用户成功", target=target)
		return response

	def _validate_self_update(self, request, target):
		if target.id != request.user.id:
			return None

		if "is_active" in request.data and not _as_bool(request.data.get("is_active")):
			write_audit_log(request, "protect_block", "blocked", detail="尝试停用当前登录用户", target=target)
			return Response({"detail": "不能停用当前登录用户"}, status=status.HTTP_400_BAD_REQUEST)

		role_ids = _extract_role_ids(request.data.get("role_ids"))
		if role_ids is not None:
			write_audit_log(request, "protect_block", "blocked", detail="尝试修改当前登录用户角色", target=target)
			return Response({"detail": "不能修改当前登录用户角色"}, status=status.HTTP_400_BAD_REQUEST)

		return None

	def update(self, request, *args, **kwargs):
		target = self.get_object()
		if target.has_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试修改超级管理员用户", target=target)
			return Response({"detail": "禁止操作超级管理员用户"}, status=status.HTTP_403_FORBIDDEN)
		self_check = self._validate_self_update(request, target)
		if self_check is not None:
			return self_check
		response = super().update(request, *args, **kwargs)
		notify_users_permission_context_changed([target.id], reason="user_roles_updated")
		write_audit_log(request, "update", "success", detail="更新用户成功", target=target)
		return response

	def partial_update(self, request, *args, **kwargs):
		target = self.get_object()
		if target.has_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试部分修改超级管理员用户", target=target)
			return Response({"detail": "禁止操作超级管理员用户"}, status=status.HTTP_403_FORBIDDEN)
		self_check = self._validate_self_update(request, target)
		if self_check is not None:
			return self_check
		response = super().partial_update(request, *args, **kwargs)
		notify_users_permission_context_changed([target.id], reason="user_roles_updated")
		write_audit_log(request, "update", "success", detail="部分更新用户成功", target=target)
		return response

	def destroy(self, request, *args, **kwargs):
		target = self.get_object()
		if target.has_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试删除超级管理员用户", target=target)
			return Response({"detail": "超级管理员用户禁止删除"}, status=status.HTTP_403_FORBIDDEN)
		if target.id == request.user.id:
			write_audit_log(request, "protect_block", "blocked", detail="尝试删除当前登录用户", target=target)
			return Response({"detail": "不能删除当前登录用户"}, status=status.HTTP_400_BAD_REQUEST)
		notify_user_force_logout(target.id, request.user.username)
		response = super().destroy(request, *args, **kwargs)
		write_audit_log(request, "delete", "success", detail="删除用户成功", target=target)
		return response

	def get_queryset(self):
		ensure_super_admin_role()
		ensure_default_role()
		queryset = super().get_queryset()
		keyword = self.request.query_params.get("keyword", "").strip()
		created_from = self.request.query_params.get("created_from", "").strip()
		created_to = self.request.query_params.get("created_to", "").strip()
		if keyword:
			queryset = queryset.filter(
				Q(username__icontains=keyword)
				| Q(display_name__icontains=keyword)
				| Q(email__icontains=keyword)
			)
		if created_from:
			queryset = queryset.filter(created_at__gte=created_from)
		if created_to:
			queryset = queryset.filter(created_at__lte=created_to)
		return queryset

	@action(detail=True, methods=["post"], url_path="kickout")
	def kickout(self, request, pk=None):
		if not request.user.is_superuser:
			write_audit_log(request, "kickout", "blocked", detail="非超级管理员尝试踢人下线")
			raise_permission_denied()

		target = self.get_object()
		if target.has_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试踢超级管理员用户下线", target=target)
			return Response({"detail": "禁止操作超级管理员用户"}, status=status.HTTP_403_FORBIDDEN)
		if target.id == request.user.id:
			write_audit_log(request, "kickout", "blocked", detail="尝试踢自己下线", target=target)
			return Response({"detail": "不能踢自己下线"}, status=status.HTTP_400_BAD_REQUEST)

		notify_user_force_logout(target.id, request.user.username)
		write_audit_log(request, "kickout", "success", detail="踢用户下线成功", target=target)
		return Response({"detail": "已通知目标用户下线"})


class RoleViewSet(viewsets.ModelViewSet):
	queryset = Role.objects.prefetch_related("permissions").all().order_by("-id")
	serializer_class = RoleSerializer
	permission_classes = [IsAuthenticated, ActionPermission]
	required_permission_map = {
		"list": "user.view_role",
		"retrieve": "user.view_role",
		"create": "user.create_role",
		"update": "user.update_role",
		"partial_update": "user.update_role",
		"destroy": "user.delete_role",
	}

	def get_queryset(self):
		ensure_super_admin_role()
		ensure_default_role()
		queryset = super().get_queryset()
		if not self.request.user.is_superuser:
			queryset = queryset.exclude(permissions__code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES)
		keyword = self.request.query_params.get("keyword", "").strip()
		if keyword:
			queryset = queryset.filter(
				Q(name__icontains=keyword)
				| Q(description__icontains=keyword)
				| Q(permissions__name__icontains=keyword)
				| Q(permissions__code__icontains=keyword)
			).distinct()
		return queryset

	def create(self, request, *args, **kwargs):
		requested_permission_ids = request.data.get("permission_ids") or []
		if not request.user.is_superuser and Permission.objects.filter(id__in=requested_permission_ids, code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES).exists():
			raise_permission_denied()
		response = super().create(request, *args, **kwargs)
		target = Role.objects.filter(pk=response.data.get("id")).first() if isinstance(response.data, dict) else None
		if target is not None:
			notify_users_permission_context_changed(
				User.objects.filter(roles=target).values_list("id", flat=True),
				reason="role_permissions_updated",
			)
		write_audit_log(request, "create", "success", detail="创建角色成功", target=target)
		return response

	def update(self, request, *args, **kwargs):
		role = self.get_object()
		affected_user_ids = list(User.objects.filter(roles=role).values_list("id", flat=True))
		if not request.user.is_superuser and role.permissions.filter(code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES).exists():
			raise_permission_denied()
		if role.is_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试编辑超级管理员角色", target=role)
			return Response({"detail": "超级管理员角色禁止编辑"}, status=status.HTTP_403_FORBIDDEN)
		response = super().update(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="role_permissions_updated")
		write_audit_log(request, "update", "success", detail="更新角色成功", target=role)
		return response

	def partial_update(self, request, *args, **kwargs):
		role = self.get_object()
		affected_user_ids = list(User.objects.filter(roles=role).values_list("id", flat=True))
		if not request.user.is_superuser and role.permissions.filter(code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES).exists():
			raise_permission_denied()
		if role.is_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试部分编辑超级管理员角色", target=role)
			return Response({"detail": "超级管理员角色禁止编辑"}, status=status.HTTP_403_FORBIDDEN)
		response = super().partial_update(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="role_permissions_updated")
		write_audit_log(request, "update", "success", detail="部分更新角色成功", target=role)
		return response

	def destroy(self, request, *args, **kwargs):
		role = self.get_object()
		affected_user_ids = list(User.objects.filter(roles=role).values_list("id", flat=True))
		if not request.user.is_superuser and role.permissions.filter(code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES).exists():
			raise_permission_denied()
		if role.is_super_admin_role():
			write_audit_log(request, "protect_block", "blocked", detail="尝试删除超级管理员角色", target=role)
			return Response({"detail": "超级管理员角色禁止删除"}, status=status.HTTP_403_FORBIDDEN)
		if request.user.roles.filter(id=role.id).exists():
			write_audit_log(request, "protect_block", "blocked", detail="尝试删除当前用户正在使用的角色", target=role)
			return Response({"detail": "不能删除当前用户正在使用的角色"}, status=status.HTTP_400_BAD_REQUEST)
		orphan_usernames = list(
			User.objects.filter(roles=role)
			.annotate(role_count=Count("roles"))
			.filter(role_count__lte=1)
			.values_list("username", flat=True)[:5]
		)
		if orphan_usernames:
			write_audit_log(request, "protect_block", "blocked", detail="尝试删除导致用户失去全部角色的角色", target=role)
			return Response({"detail": f"删除后将导致以下用户失去角色：{', '.join(orphan_usernames)}"}, status=status.HTTP_400_BAD_REQUEST)
		response = super().destroy(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="role_deleted")
		write_audit_log(request, "delete", "success", detail="删除角色成功", target=role)
		return response


class PermissionViewSet(viewsets.ModelViewSet):
	queryset = Permission.objects.all().order_by("-id")
	serializer_class = PermissionSerializer
	permission_classes = [IsAuthenticated, ActionPermission]
	required_permission_map = {
		"list": "user.view_permission",
		"retrieve": "user.view_permission",
		"create": "user.create_permission",
		"update": "user.update_permission",
		"partial_update": "user.update_permission",
		"destroy": "user.delete_permission",
	}

	def get_queryset(self):
		queryset = super().get_queryset()
		if not self.request.user.is_superuser:
			queryset = queryset.exclude(code__in=SUPER_ADMIN_ONLY_PERMISSION_CODES)
		keyword = self.request.query_params.get("keyword", "").strip()
		if keyword:
			queryset = queryset.filter(
				Q(code__icontains=keyword)
				| Q(name__icontains=keyword)
				| Q(description__icontains=keyword)
			)
		return queryset

	def create(self, request, *args, **kwargs):
		permission_code = str(request.data.get("code", "")).strip()
		if (_is_core_permission_code(permission_code) or permission_code in SUPER_ADMIN_ONLY_PERMISSION_CODES) and not request.user.is_superuser:
			write_audit_log(request, "protect_block", "blocked", detail="非超级管理员尝试创建核心权限", metadata={"code": permission_code})
			raise_permission_denied("核心权限仅超级管理员可操作")
		response = super().create(request, *args, **kwargs)
		target = Permission.objects.filter(pk=response.data.get("id")).first() if isinstance(response.data, dict) else None
		if target is not None:
			affected_user_ids = User.objects.filter(roles__permissions=target).distinct().values_list("id", flat=True)
			notify_users_permission_context_changed(affected_user_ids, reason="permission_created")
		write_audit_log(request, "create", "success", detail="创建权限成功", target=target)
		return response

	def update(self, request, *args, **kwargs):
		target = self.get_object()
		affected_user_ids = list(User.objects.filter(roles__permissions=target).distinct().values_list("id", flat=True))
		if (_is_core_permission_code(target.code) or target.code in SUPER_ADMIN_ONLY_PERMISSION_CODES) and not request.user.is_superuser:
			write_audit_log(request, "protect_block", "blocked", detail="非超级管理员尝试更新核心权限", target=target)
			raise_permission_denied("核心权限仅超级管理员可操作")
		response = super().update(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="permission_updated")
		write_audit_log(request, "update", "success", detail="更新权限成功", target=target)
		return response

	def partial_update(self, request, *args, **kwargs):
		target = self.get_object()
		affected_user_ids = list(User.objects.filter(roles__permissions=target).distinct().values_list("id", flat=True))
		if (_is_core_permission_code(target.code) or target.code in SUPER_ADMIN_ONLY_PERMISSION_CODES) and not request.user.is_superuser:
			write_audit_log(request, "protect_block", "blocked", detail="非超级管理员尝试部分更新核心权限", target=target)
			raise_permission_denied("核心权限仅超级管理员可操作")
		response = super().partial_update(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="permission_updated")
		write_audit_log(request, "update", "success", detail="部分更新权限成功", target=target)
		return response

	def destroy(self, request, *args, **kwargs):
		target = self.get_object()
		affected_user_ids = list(User.objects.filter(roles__permissions=target).distinct().values_list("id", flat=True))
		if (_is_core_permission_code(target.code) or target.code in SUPER_ADMIN_ONLY_PERMISSION_CODES) and not request.user.is_superuser:
			write_audit_log(request, "protect_block", "blocked", detail="非超级管理员尝试删除核心权限", target=target)
			raise_permission_denied("核心权限仅超级管理员可操作")
		response = super().destroy(request, *args, **kwargs)
		notify_users_permission_context_changed(affected_user_ids, reason="permission_deleted")
		write_audit_log(request, "delete", "success", detail="删除权限成功", target=target)
		return response


class JwtTokenRefreshView(TokenRefreshView):
	pass
