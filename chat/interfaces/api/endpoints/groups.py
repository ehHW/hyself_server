from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.commands import (
    execute_apply_group_invitation_command,
    execute_disband_group_conversation_command,
    execute_handle_group_join_request_command,
    execute_invite_group_member_command,
    execute_leave_group_conversation_command,
    execute_mute_group_member_command,
    execute_remove_group_member_command,
    execute_transfer_group_owner_command,
    execute_update_group_config_command,
    execute_update_group_member_role_command,
)
from chat.application.queries import execute_list_group_join_requests_query, execute_list_group_members_query
from chat.interfaces.api.serializer_scenes.groups import (
    ApplyGroupInvitationSerializer,
    GroupConfigUpdateSerializer,
    GroupJoinRequestHandleSerializer,
    InviteConversationMemberSerializer,
    MuteConversationMemberSerializer,
    TransferGroupOwnerSerializer,
    UpdateConversationMemberRoleSerializer,
)
from chat.models import ChatConversation, ChatConversationMember, ChatGroupJoinRequest


User = get_user_model()


class ConversationMembersAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(execute_list_group_members_query(request.user, conversation_id))


class ConversationInviteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        serializer = InviteConversationMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload, response_status = execute_invite_group_member_command(request.user, conversation_id, serializer.validated_data["target_user_id"])
        except User.DoesNotExist:
            return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=response_status)


class GroupInvitationApplyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ensure_request_permission(request, "chat.view_conversation")
        serializer = ApplyGroupInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload, response_status = execute_apply_group_invitation_command(
                request.user,
                serializer.validated_data["conversation_id"],
                serializer.validated_data.get("inviter_user_id"),
            )
        except ChatConversation.DoesNotExist:
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"detail": "邀请人不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=response_status)


class ConversationLeaveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        ensure_request_permission(request, "chat.view_conversation")
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_leave_group_conversation_command(request.user, conversation_id)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class ConversationTransferOwnerAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        serializer = TransferGroupOwnerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_transfer_group_owner_command(request.user, conversation_id, serializer.validated_data["target_user_id"])
        except ChatConversationMember.DoesNotExist:
            return Response({"detail": "目标成员不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class ConversationDisbandAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_disband_group_conversation_command(request.user, conversation_id)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationRemoveMemberAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int, user_id: int):
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_remove_group_member_command(request.user, conversation_id, user_id)
        except ChatConversationMember.DoesNotExist:
            return Response({"detail": "目标成员不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class ConversationUpdateMemberRoleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int, user_id: int):
        serializer = UpdateConversationMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_update_group_member_role_command(request.user, conversation_id, user_id, serializer.validated_data["role"])
        except ChatConversationMember.DoesNotExist:
            return Response({"detail": "目标成员不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class ConversationMuteMemberAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int, user_id: int):
        serializer = MuteConversationMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_mute_group_member_command(
                request.user,
                conversation_id,
                user_id,
                serializer.validated_data["mute_minutes"],
                serializer.validated_data.get("reason", ""),
            )
        except ChatConversationMember.DoesNotExist:
            return Response({"detail": "目标成员不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class GroupConfigAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, conversation_id: int):
        conversation = ChatConversation.objects.select_related("group_config").filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).first()
        if conversation is None or not hasattr(conversation, "group_config"):
            return Response({"detail": "群配置不存在"}, status=status.HTTP_404_NOT_FOUND)
        serializer = GroupConfigUpdateSerializer(conversation.group_config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_update_group_config_command(request.user, conversation_id, serializer.validated_data)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class GroupJoinRequestListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        conversation_id = request.query_params.get("conversation_id")
        status_filter = str(request.query_params.get("status", "")).strip()
        payload = execute_list_group_join_requests_query(request.user, int(conversation_id) if conversation_id else None, status_filter)
        return Response(payload)


class GroupJoinRequestHandleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id: int):
        serializer = GroupJoinRequestHandleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not ChatGroupJoinRequest.objects.filter(id=request_id).exists():
            return Response({"detail": "群审批记录不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_handle_group_join_request_command(
                request.user,
                request_id,
                serializer.validated_data["action"],
                serializer.validated_data.get("review_note", ""),
            )
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)