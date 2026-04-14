from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.commands import (
    execute_delete_friend_command,
    execute_handle_friend_request_command,
    execute_submit_friend_request_command,
    execute_update_friend_setting_command,
)
from chat.application.queries import ListFriendRequestsQueryParams, execute_list_friend_requests_query, execute_list_friends_query
from chat.domain.friendships import get_active_friendship_between
from chat.interfaces.api.serializer_scenes.friends import FriendRequestCreateSerializer, FriendRequestHandleSerializer, FriendSettingUpdateSerializer
from chat.models import ChatFriendRequest, ChatFriendship, build_pair_key


User = get_user_model()


class FriendRequestListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        direction = str(request.query_params.get("direction", "received")).strip().lower()
        status_filter = str(request.query_params.get("status", "")).strip()
        payload = execute_list_friend_requests_query(request.user, ListFriendRequestsQueryParams(direction=direction, status_filter=status_filter))
        return Response(payload)

    def post(self, request):
        ensure_request_permission(request, "chat.add_friend")
        serializer = FriendRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = execute_submit_friend_request_command(request.user, serializer.validated_data["to_user_id"], serializer.validated_data.get("request_message", ""))
        except User.DoesNotExist:
            return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(result.payload, status=result.status_code)


class FriendRequestHandleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id: int):
        if not ChatFriendRequest.objects.filter(id=request_id).exists():
            return Response({"detail": "好友申请不存在"}, status=status.HTTP_404_NOT_FOUND)
        serializer = FriendRequestHandleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = execute_handle_friend_request_command(request.user, request_id, serializer.validated_data["action"])
        return Response(payload)


class FriendListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ensure_request_permission(request, "chat.view_conversation")
        keyword = str(request.query_params.get("keyword", "")).strip()
        return Response(execute_list_friends_query(request.user, keyword))


class FriendDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, friend_user_id: int):
        ensure_request_permission(request, "chat.delete_friend")
        if get_active_friendship_between(request.user.id, friend_user_id) is None:
            return Response({"detail": "好友关系不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(execute_delete_friend_command(request.user, friend_user_id))


class FriendSettingAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, friend_user_id: int):
        if not ChatFriendship.objects.filter(pair_key=build_pair_key(request.user.id, friend_user_id)).exists():
            return Response({"detail": "好友关系不存在"}, status=status.HTTP_404_NOT_FOUND)
        serializer = FriendSettingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(execute_update_friend_setting_command(request.user, friend_user_id, remark=serializer.validated_data.get("remark")))