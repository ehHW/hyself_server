from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone


def build_event(event_type: str, payload: dict | None = None, *, domain: str = "chat") -> dict:
    return {
        "type": "event",
        "event_type": event_type,
        "domain": domain,
        "occurred_at": timezone.now().isoformat(),
        "payload": payload or {},
    }


def publish_user_event(user_id: int, event_type: str, payload: dict | None = None, *, domain: str) -> None:
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        f"ws_user_{user_id}",
        {
            "type": "system.event",
            "payload": build_event(event_type, payload, domain=domain),
        },
    )