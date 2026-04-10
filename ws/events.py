"""WebSocket 事件广播兼容入口。"""

from chat.infrastructure.event_bus import (
    notify_chat_conversation_updated,
    notify_chat_friend_request_updated,
    notify_chat_friendship_updated,
    notify_chat_group_join_request_updated,
    notify_chat_message_updated,
    notify_chat_new_message,
    notify_chat_system_notice,
    notify_chat_typing,
    notify_chat_unread_updated,
)
from ws.event_bus import build_event as build_ws_event, publish_user_event


def notify_user_force_logout(user_id: int, operator_username: str) -> None:
    """
    向用户发送强制下线通知
    
    Args:
        user_id: 目标用户ID
        operator_username: 操作员用户名
    """
    publish_user_event(
        user_id,
        "system.force_logout",
        {
            "message": f"您已被管理员 {operator_username} 踢下线",
        },
        domain="system",
    )
