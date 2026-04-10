from auth.permissions import AuthenticatedPermission as IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.queries import AdminConversationListQueryParams, AdminMessageListQueryParams, execute_admin_conversation_list_query, execute_admin_message_list_query


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