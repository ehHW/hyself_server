from django.urls import include, path
from rest_framework.routers import DefaultRouter

from user.views import (
    JwtTokenRefreshView,
    PermissionViewSet,
    RoleViewSet,
    UserViewSet,
    change_password_view,
    login_view,
    permission_context_view,
    profile_view,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="users")
router.register("roles", RoleViewSet, basename="roles")
router.register("permissions", PermissionViewSet, basename="permissions")

urlpatterns = [
    path("auth/login/", login_view, name="login"),
    path("auth/refresh/", JwtTokenRefreshView.as_view(), name="token_refresh"),
    path("auth/profile/", profile_view, name="profile"),
    path("auth/permission-context/", permission_context_view, name="permission_context"),
    path("auth/change-password/", change_password_view, name="change_password"),
    path("", include(router.urls)),
]
