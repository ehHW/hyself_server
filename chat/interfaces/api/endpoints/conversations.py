from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.commands import (
    execute_create_group_conversation_command,
    execute_delete_message_for_user_command,
    execute_forward_messages_command,
    execute_hide_conversation_command,
    execute_mark_conversation_read_command,
    execute_open_direct_conversation_command,
    execute_restore_revoked_draft_command,
    execute_revoke_message_command,
    execute_send_asset_message_command,
    execute_toggle_conversation_pin_command,
    execute_update_conversation_preference_command,
)
from chat.application.queries import ConversationMessagesQueryParams, ListConversationsQueryParams, execute_conversation_detail_query, execute_conversation_messages_query, execute_list_conversations_query
from chat.infrastructure.event_bus import notify_chat_unread_updated
from chat.interfaces.api.serializer_scenes.conversations import (
    AttachmentMessageCreateSerializer,
    ConversationPinSerializer,
    ConversationPreferenceSerializer,
    ConversationReadSerializer,
    CreateGroupConversationSerializer,
    ForwardMessagesSerializer,
    OpenDirectConversationSerializer,
)
from chat.models import ChatConversation, ChatMessage


User = get_user_model()


class ConversationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ensure_request_permission(request, "chat.view_conversation")
        category = str(request.query_params.get("category", "all")).strip().lower()
        keyword = str(request.query_params.get("keyword", "")).strip()
        include_hidden = str(request.query_params.get("include_hidden", "false")).strip().lower() in {"1", "true", "yes", "on"}
        payload = execute_list_conversations_query(
            request.user,
            ListConversationsQueryParams(category=category, keyword=keyword, include_hidden=include_hidden),
        )
        return Response(payload)


class ConversationDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        try:
            payload = execute_conversation_detail_query(request.user, conversation_id)
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class DirectConversationOpenAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "chat.view_conversation")
        serializer = OpenDirectConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user = User.objects.filter(id=serializer.validated_data["target_user_id"], deleted_at__isnull=True, is_active=True).first()
        if target_user is None:
            return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)
        if target_user.id == request.user.id:
            return Response({"detail": "不能和自己发起单聊"}, status=status.HTTP_400_BAD_REQUEST)
        result = execute_open_direct_conversation_command(request.user, target_user)
        return Response({"detail": "会话已打开", "created": False, "conversation": result.conversation})


class GroupConversationCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "chat.create_group")
        serializer = CreateGroupConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member_ids = sorted(set(serializer.validated_data.get("member_user_ids", [])))
        member_users = list(User.objects.filter(id__in=member_ids, deleted_at__isnull=True, is_active=True))
        result = execute_create_group_conversation_command(
            request.user,
            name=serializer.validated_data["name"],
            member_users=member_users,
            join_approval_required=serializer.validated_data["join_approval_required"],
            allow_member_invite=serializer.validated_data["allow_member_invite"],
        )
        return Response({"detail": "群聊创建成功", "conversation": result.conversation}, status=status.HTTP_201_CREATED)


class ConversationHideAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.hide_conversation")
        try:
            payload = execute_hide_conversation_command(request.user, conversation_id)
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        serializer = ConversationReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_mark_conversation_read_command(request.user, conversation_id, last_read_sequence=serializer.validated_data["last_read_sequence"])
        except ValidationError as exc:
            detail = getattr(exc, "detail", {})
            if isinstance(detail, dict) and detail.get("detail") == "会话不存在":
                return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
            raise
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        notify_chat_unread_updated(request.user.id, payload["conversation_id"], payload["unread_count"], payload["total_unread_count"])
        return Response({"detail": "已标记为已读", **payload})


class ConversationMessagesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        try:
            payload = execute_conversation_messages_query(
                request.user,
                conversation_id,
                ConversationMessagesQueryParams(
                    before_sequence=int(request.query_params.get("before_sequence")) if request.query_params.get("before_sequence") else None,
                    after_sequence=int(request.query_params.get("after_sequence")) if request.query_params.get("after_sequence") else None,
                    around_sequence=int(request.query_params.get("around_sequence")) if request.query_params.get("around_sequence") else None,
                    limit=max(1, min(100, int(request.query_params.get("limit", 30)))),
                ),
            )
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class ConversationAttachmentMessageAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.send_attachment")
        serializer = AttachmentMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_send_asset_message_command(
                request.user,
                conversation_id,
                source_asset_reference_id=serializer.validated_data["asset_reference_id"],
            )
        except ValidationError as exc:
            detail = getattr(exc, "detail", {})
            if isinstance(detail, dict) and detail.get("detail") == "会话不存在":
                return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload, status=status.HTTP_201_CREATED)


class MessageForwardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "chat.forward_message")
        serializer = ForwardMessagesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_forward_messages_command(
                request.user,
                target_conversation_id=serializer.validated_data["target_conversation_id"],
                message_ids=serializer.validated_data["message_ids"],
                forward_mode=serializer.validated_data["forward_mode"],
            )
        except ValidationError as exc:
            return Response(getattr(exc, "detail", {"detail": "请求参数非法"}), status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class MessageRevokeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id: int):
        ensure_request_permission(request, "chat.revoke_message")
        try:
            payload = execute_revoke_message_command(request.user, message_id)
        except ChatMessage.DoesNotExist:
            return Response({"detail": "消息不存在"}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError as exc:
            return Response(getattr(exc, "detail", {"detail": "请求参数非法"}), status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class MessageRestoreDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id: int):
        ensure_request_permission(request, "chat.restore_revoked_message")
        try:
            payload = execute_restore_revoked_draft_command(request.user, message_id)
        except ChatMessage.DoesNotExist:
            return Response({"detail": "消息不存在"}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError as exc:
            return Response(getattr(exc, "detail", {"detail": "请求参数非法"}), status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class MessageDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id: int):
        ensure_request_permission(request, "chat.delete_message")
        try:
            payload = execute_delete_message_for_user_command(request.user, message_id)
        except ChatMessage.DoesNotExist:
            return Response({"detail": "消息不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationPreferenceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        serializer = ConversationPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_update_conversation_preference_command(
                request.user,
                conversation_id,
                mute_notifications=serializer.validated_data.get("mute_notifications"),
                group_nickname=serializer.validated_data.get("group_nickname"),
            )
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationPinAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.pin_conversation")
        serializer = ConversationPinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_toggle_conversation_pin_command(request.user, conversation_id, is_pinned=serializer.validated_data["is_pinned"])
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)