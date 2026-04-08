"""
WebSocket 认证中间件
"""
from urllib.parse import parse_qs

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
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token = params.get("token", [""])[0]

        scope["user"] = None
        if token:
            scope["user"] = await get_user(token)

        return await super().__call__(scope, receive, send)
