"""
WebSocket 认证中间件
"""
from urllib.parse import parse_qs

from django.contrib.auth.models import AnonymousUser
from auth.jwt import get_user_from_jwt_token
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware


@database_sync_to_async
def get_user(token: str):
    """根据 JWT token 解析当前用户。"""
    return get_user_from_jwt_token(token)


class JwtAuthMiddleware(BaseMiddleware):
    """JWT 认证中间件，从查询参数中提取和验证 token"""
    
    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token = params.get("token", [""])[0]

        user = AnonymousUser()
        if token:
            user = await get_user(token) or AnonymousUser()

        scope["user"] = user

        return await self.inner(scope, receive, send)
