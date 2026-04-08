from auth.permissions import AuthenticatedPermission as IsAuthenticated
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.commands import (
    execute_apply_group_invitation_command,
    execute_create_group_conversation_command,
    execute_delete_friend_command,
    execute_forward_messages_command,
    execute_handle_friend_request_command,
    execute_handle_group_join_request_command,
    execute_hide_conversation_command,
    execute_invite_group_member_command,
    execute_leave_group_conversation_command,
    execute_mark_conversation_read_command,
    execute_mute_group_member_command,
    execute_open_direct_conversation_command,
    execute_remove_group_member_command,
    execute_send_asset_message_command,
    execute_submit_friend_request_command,
    execute_toggle_conversation_pin_command,
    execute_update_chat_settings_command,
    execute_update_conversation_preference_command,
    execute_update_friend_setting_command,
    execute_update_group_config_command,
    execute_update_group_member_role_command,
)
from chat.application.queries import (
    AdminConversationListQueryParams,
    AdminMessageListQueryParams,
    ChatSearchQueryParams,
    ConversationMessagesQueryParams,
    ListConversationsQueryParams,
    ListFriendRequestsQueryParams,
    execute_admin_conversation_list_query,
    execute_admin_message_list_query,
    execute_chat_search_query,
    execute_conversation_detail_query,
    execute_conversation_messages_query,
    execute_get_chat_settings_query,
    execute_list_conversations_query,
    execute_list_friend_requests_query,
    execute_list_friends_query,
    execute_list_group_join_requests_query,
    execute_list_group_members_query,
)
from chat.domain.preferences import get_or_create_user_preference
from chat.domain.access import get_conversation_access, get_member
from chat.domain.friendships import get_active_friendship_between
from chat.domain.serialization import serialize_conversation
from chat.models import ChatConversation, ChatConversationMember, ChatFriendRequest, ChatFriendship, ChatGroupJoinRequest, ChatMessage, build_pair_key
from chat.serializers import (
    AttachmentMessageCreateSerializer,
    ApplyGroupInvitationSerializer,
    ConversationPreferenceSerializer,
    ConversationReadSerializer,
    CreateGroupConversationSerializer,
    ForwardMessagesSerializer,
    FriendSettingUpdateSerializer,
    FriendRequestCreateSerializer,
    FriendRequestHandleSerializer,
    GroupConfigUpdateSerializer,
    GroupJoinRequestHandleSerializer,
    InviteConversationMemberSerializer,
    MuteConversationMemberSerializer,
    ConversationPinSerializer,
    OpenDirectConversationSerializer,
    UpdateConversationMemberRoleSerializer,
    UserPreferenceSerializer,
)
from ws.events import (
    notify_chat_conversation_updated,
    notify_chat_system_notice,
    notify_chat_unread_updated,
)


User = get_user_model()


class FriendRequestListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        direction = str(request.query_params.get("direction", "received")).strip().lower()
        status_filter = str(request.query_params.get("status", "")).strip()
        payload = execute_list_friend_requests_query(request.user, ListFriendRequestsQueryParams(direction=direction, status_filter=status_filter))
        return Response(payload)

    def post(self, request):
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
        keyword = str(request.query_params.get("keyword", "")).strip()
        return Response(execute_list_friends_query(request.user, keyword))


class FriendDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, friend_user_id: int):
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


class ConversationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
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
        try:
            payload = execute_conversation_detail_query(request.user, conversation_id)
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class DirectConversationOpenAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OpenDirectConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user = User.objects.filter(id=serializer.validated_data["target_user_id"], deleted_at__isnull=True, is_active=True).first()
        if target_user is None:
            return Response({"detail": "目标用户不存在"}, status=status.HTTP_404_NOT_FOUND)
        if target_user.id == request.user.id:
            return Response({"detail": "不能和自己发起单聊"}, status=status.HTTP_400_BAD_REQUEST)
        result = execute_open_direct_conversation_command(request.user, target_user)
        return Response({"detail": "会话已打开", "created": False, "conversation": {"id": result.conversation_id, "type": result.conversation_type, "show_in_list": result.show_in_list}})


class GroupConversationCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
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
        return Response({"detail": "群聊创建成功", "conversation": {"id": result.conversation_id, "type": result.conversation_type, "name": result.name}}, status=status.HTTP_201_CREATED)


class ConversationHideAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        try:
            payload = execute_hide_conversation_command(request.user, conversation_id)
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError:
            return Response({"detail": "当前无权操作该会话"}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        serializer = ConversationReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_mark_conversation_read_command(request.user, conversation_id, last_read_sequence=serializer.validated_data["last_read_sequence"])
        except ValidationError as exc:
            detail = getattr(exc, "detail", {})
            if isinstance(detail, dict) and detail.get("detail") == "会话不存在":
                return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
            raise
        except PermissionDenied:
            return Response({"detail": "当前无权操作该会话"}, status=status.HTTP_403_FORBIDDEN)
        notify_chat_unread_updated(request.user.id, payload["conversation_id"], payload["unread_count"], payload["total_unread_count"])
        return Response({"detail": "已标记为已读", **payload})


class ConversationMessagesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id: int):
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE).exists():
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
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
        return Response(payload)


class ConversationAttachmentMessageAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
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
        serializer = ForwardMessagesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_forward_messages_command(
                request.user,
                target_conversation_id=serializer.validated_data["target_conversation_id"],
                message_ids=serializer.validated_data["message_ids"],
            )
        except ValidationError as exc:
            return Response(getattr(exc, "detail", {"detail": "请求参数非法"}), status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationPreferenceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, conversation_id: int):
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
        except PermissionError:
            return Response({"detail": "当前无权操作该会话"}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class ConversationMembersAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id: int):
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
        serializer = ApplyGroupInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload, response_status = execute_apply_group_invitation_command(
                request.user,
                serializer.validated_data["conversation_id"],
                serializer.validated_data["inviter_user_id"],
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
        if not ChatConversation.objects.filter(id=conversation_id, status=ChatConversation.Status.ACTIVE, type=ChatConversation.Type.GROUP).exists():
            return Response({"detail": "群聊不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = execute_leave_group_conversation_command(request.user, conversation_id)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
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


class ConversationPinAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id: int):
        serializer = ConversationPinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = execute_toggle_conversation_pin_command(request.user, conversation_id, is_pinned=serializer.validated_data["is_pinned"])
        except ChatConversation.DoesNotExist:
            return Response({"detail": "会话不存在"}, status=status.HTTP_404_NOT_FOUND)
        except PermissionError:
            return Response({"detail": "当前无权操作该会话"}, status=status.HTTP_403_FORBIDDEN)
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


class ChatSearchAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = execute_chat_search_query(
            request.user,
            ChatSearchQueryParams(
                keyword=str(request.query_params.get("keyword", "")).strip(),
                limit=max(1, min(20, int(request.query_params.get("limit", 5)))),
            ),
        )
        return Response(payload)


class ChatSettingsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(execute_get_chat_settings_query(request.user))

    def patch(self, request):
        preference = get_or_create_user_preference(request.user)
        serializer = UserPreferenceSerializer(preference, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(execute_update_chat_settings_command(request.user, data=serializer.validated_data))


class AdminConversationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = execute_admin_conversation_list_query(
            request.user,
            AdminConversationListQueryParams(
                keyword=str(request.query_params.get("keyword", "")).strip(),
                conversation_type=str(request.query_params.get("type", "")).strip(),
            ),
        )
        return Response(payload)


class AdminMessageListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = execute_admin_message_list_query(
            request.user,
            AdminMessageListQueryParams(
                conversation_id=int(request.query_params.get("conversation_id")) if request.query_params.get("conversation_id") else None,
                keyword=str(request.query_params.get("keyword", "")).strip(),
            ),
        )
        return Response(payload)
