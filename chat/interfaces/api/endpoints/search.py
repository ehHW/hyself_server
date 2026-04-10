from auth.permissions import AuthenticatedPermission as IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.queries import ChatSearchQueryParams, execute_chat_search_query


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