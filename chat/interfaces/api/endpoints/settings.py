from auth.permissions import AuthenticatedPermission as IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.application.commands import execute_update_chat_settings_command
from chat.application.queries import execute_get_chat_settings_query
from chat.domain.preferences import get_or_create_user_preference
from chat.interfaces.api.serializer_scenes.settings import UserPreferenceSerializer


class ChatSettingsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(execute_get_chat_settings_query(request.user))

    def patch(self, request):
        preference = get_or_create_user_preference(request.user)
        serializer = UserPreferenceSerializer(preference, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(execute_update_chat_settings_command(request.user, data=serializer.validated_data))