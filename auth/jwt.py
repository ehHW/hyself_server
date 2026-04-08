from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken


User = get_user_model()


def get_user_from_jwt_token(token: str):
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return None
    try:
        validated = UntypedToken(normalized_token)
    except (InvalidToken, TokenError):
        return None
    user_id = validated.payload.get("user_id")
    if user_id is None:
        return None
    return User.objects.filter(id=user_id).first()