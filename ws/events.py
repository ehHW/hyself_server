"""
WebSocket 事件广播模块
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone


def build_ws_event(event_type: str, payload: dict | None = None, *, domain: str = "chat") -> dict:
    return {
        "type": "event",
        "event_type": event_type,
        "domain": domain,
        "occurred_at": timezone.now().isoformat(),
        "payload": payload or {},
    }


def _send_user_payload(user_id: int, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        f"ws_user_{user_id}",
        {
            "type": "system.event",
            "payload": payload,
        },
    )


def notify_user_force_logout(user_id: int, operator_username: str) -> None:
    """
    向用户发送强制下线通知
    
    Args:
        user_id: 目标用户ID
        operator_username: 操作员用户名
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    async_to_sync(channel_layer.group_send)(
        f"ws_user_{user_id}",
        {
            "type": "system.event",
            "payload": build_ws_event(
                "system.force_logout",
                {
                "message": f"您已被管理员 {operator_username} 踢下线",
                },
                domain="system",
            ),
        },
    )


def notify_chat_new_message(user_id: int, message_payload: dict) -> None:
    _send_user_payload(
        user_id,
        build_ws_event(
            "chat.message.created",
            {
                "conversation_id": message_payload.get("conversation_id"),
                "message": message_payload.get("message"),
            },
        ),
    )


def notify_chat_conversation_updated(user_id: int, conversation_payload: dict) -> None:
    _send_user_payload(user_id, build_ws_event("chat.conversation.updated", {"conversation": conversation_payload}))


def notify_chat_unread_updated(user_id: int, conversation_id: int, unread_count: int, total_unread_count: int | None = None) -> None:
    _send_user_payload(
        user_id,
        build_ws_event(
            "chat.unread.updated",
            {
                "conversation_id": conversation_id,
                "unread_count": unread_count,
                "total_unread_count": total_unread_count if total_unread_count is not None else unread_count,
            },
        ),
    )


def notify_chat_friend_request_updated(user_id: int, request_payload: dict) -> None:
    _send_user_payload(user_id, build_ws_event("chat.friend_request.updated", {"request": request_payload}))


def notify_chat_friendship_updated(user_id: int, payload: dict) -> None:
    _send_user_payload(user_id, build_ws_event("chat.friendship.updated", payload))


def notify_chat_group_join_request_updated(user_id: int, payload: dict) -> None:
    _send_user_payload(user_id, build_ws_event("chat.group_join_request.updated", {"join_request": payload}))


def notify_chat_typing(user_id: int, payload: dict) -> None:
    _send_user_payload(user_id, build_ws_event("chat.typing.updated", payload))


def notify_chat_system_notice(user_id: int, message: str, payload: dict | None = None) -> None:
    _send_user_payload(
        user_id,
        build_ws_event(
            "chat.system_notice.created",
            {
                "category": "chat",
                "message": message,
                "payload": payload or {},
            },
        ),
    )
