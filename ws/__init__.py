"""WebSocket package exports kept lazy to avoid import cycles."""

__all__ = ["JwtAuthMiddleware", "GlobalWebSocketConsumer", "notify_user_force_logout", "websocket_urlpatterns"]


def __getattr__(name: str):
    if name == "JwtAuthMiddleware":
        from ws.auth import JwtAuthMiddleware

        return JwtAuthMiddleware
    if name == "GlobalWebSocketConsumer":
        from ws.consumers import GlobalWebSocketConsumer

        return GlobalWebSocketConsumer
    if name == "notify_user_force_logout":
        from ws.events import notify_user_force_logout

        return notify_user_force_logout
    if name == "websocket_urlpatterns":
        from ws.routing import websocket_urlpatterns

        return websocket_urlpatterns
    raise AttributeError(f"module 'ws' has no attribute {name!r}")
