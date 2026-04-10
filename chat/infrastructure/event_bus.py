from __future__ import annotations

from ws.event_bus import publish_user_event


CHAT_DOMAIN = "chat"


def notify_chat_new_message(user_id: int, message_payload: dict) -> None:
    publish_user_event(
        user_id,
        "chat.message.created",
        {
            "conversation_id": message_payload.get("conversation_id"),
            "message": message_payload.get("message"),
        },
        domain=CHAT_DOMAIN,
    )


def notify_chat_conversation_updated(user_id: int, conversation_payload: dict) -> None:
    publish_user_event(user_id, "chat.conversation.updated", {"conversation": conversation_payload}, domain=CHAT_DOMAIN)


def notify_chat_unread_updated(user_id: int, conversation_id: int, unread_count: int, total_unread_count: int | None = None) -> None:
    publish_user_event(
        user_id,
        "chat.unread.updated",
        {
            "conversation_id": conversation_id,
            "unread_count": unread_count,
            "total_unread_count": total_unread_count if total_unread_count is not None else unread_count,
        },
        domain=CHAT_DOMAIN,
    )


def notify_chat_friend_request_updated(user_id: int, request_payload: dict) -> None:
    publish_user_event(user_id, "chat.friend_request.updated", {"request": request_payload}, domain=CHAT_DOMAIN)


def notify_chat_friendship_updated(user_id: int, payload: dict) -> None:
    publish_user_event(user_id, "chat.friendship.updated", payload, domain=CHAT_DOMAIN)


def notify_chat_group_join_request_updated(user_id: int, payload: dict) -> None:
    publish_user_event(user_id, "chat.group_join_request.updated", {"join_request": payload}, domain=CHAT_DOMAIN)


def notify_chat_typing(user_id: int, payload: dict) -> None:
    publish_user_event(user_id, "chat.typing.updated", payload, domain=CHAT_DOMAIN)


def notify_chat_system_notice(user_id: int, message: str, payload: dict | None = None) -> None:
    publish_user_event(
        user_id,
        "chat.system_notice.created",
        {
            "category": "chat",
            "message": message,
            "payload": payload or {},
        },
        domain=CHAT_DOMAIN,
    )


def notify_chat_message_updated(user_id: int, message_payload: dict) -> None:
    publish_user_event(
        user_id,
        "chat.message.updated",
        {
            "conversation_id": message_payload.get("conversation_id"),
            "message": message_payload.get("message"),
        },
        domain=CHAT_DOMAIN,
    )