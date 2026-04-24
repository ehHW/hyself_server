"""
WebSocket 消费者 - 处理客户端连接和消息
"""
from celery.result import AsyncResult
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework.exceptions import PermissionDenied, ValidationError

from hyself_server.celery import app as celery_app
from chat.application.commands.realtime import execute_mark_conversation_read_command, execute_send_text_message_command
from chat.application.queries.realtime import execute_chat_typing_query
from hyself.system_runtime import ensure_maintenance_activated, is_current_maintenance_active
from ws.events import build_ws_event
from ws.input_serializers import (
    ChatMarkReadWsSerializer,
    ChatSendAssetMessageWsSerializer,
    ChatSendMessageWsSerializer,
    ChatTypingWsSerializer,
    EchoWsSerializer,
    UploadTaskSubscriptionWsSerializer,
)


class GlobalWebSocketConsumer(AsyncJsonWebsocketConsumer):
    """
    全局 WebSocket 消费者，处理：
    - 用户连接/断开连接
    - ping/pong 心跳
    - 上传任务订阅管理
    - 系统事件推送（如强制下线）
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_group_name: str | None = None
        self.upload_task_groups: set[str] = set()
    
    async def connect(self):
        """处理 WebSocket 连接"""
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
            return

        maintenance_active = await database_sync_to_async(is_current_maintenance_active)()
        if maintenance_active:
            await database_sync_to_async(ensure_maintenance_activated)(actor=user if getattr(user, "is_superuser", False) else None)
            if not getattr(user, "is_superuser", False):
                await self.close(code=4403)
                return

        self.user_group_name = f"ws_user_{user.id}"
        self.upload_task_groups: set[str] = set()
        await self.accept()
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.send_json(
            {
                "type": "system",
                "message": f"WebSocket 已连接: {user.username}",
            }
        )

    async def _send_user_payload(self, user_id: int, payload: dict):
        await self.channel_layer.group_send(
            f"ws_user_{user_id}",
            {
                "type": "system.event",
                "payload": payload,
            },
        )

    async def _broadcast_chat_message(self, *, user_id: int, client_message_id: str | None, payload: dict):
        await self._send_user_payload(
            user_id,
            build_ws_event(
                "chat.message.ack",
                {
                    "conversation_id": payload["conversation_id"],
                    "client_message_id": client_message_id,
                    "message": payload["message"],
                    "conversation": payload["sender_conversation"],
                },
            ),
        )

        for recipient in payload["recipients"]:
            await self._send_user_payload(
                recipient["user_id"],
                build_ws_event(
                    "chat.message.created",
                    {
                        "conversation_id": payload["conversation_id"],
                        "message": payload["message"],
                    },
                ),
            )
            await self._send_user_payload(
                recipient["user_id"],
                build_ws_event("chat.conversation.updated", {"conversation": recipient["conversation"]}),
            )
            await self._send_user_payload(
                recipient["user_id"],
                build_ws_event(
                    "chat.unread.updated",
                    {
                        "conversation_id": payload["conversation_id"],
                        "unread_count": recipient["unread_count"],
                        "total_unread_count": recipient["total_unread_count"],
                    },
                ),
            )

    async def _validate_ws_payload(self, serializer_class, content: dict, *, event: str, invalid_message: str):
        serializer = serializer_class(data=content)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            await self._send_error(
                event=event,
                message=invalid_message,
                error_kind="schema",
                error_code=f"ws.schema.{event}.invalid",
                error_details=self._extract_error_details(exc),
            )
            return None
        return serializer.validated_data

    async def _send_error(self, *, event: str, message: str, error_kind: str, error_code: str, error_details=None):
        payload = {
            "type": "error",
            "message": message,
            "event": event,
            "error_kind": error_kind,
            "error_code": error_code,
        }
        if error_details not in (None, {}, []):
            payload["error_details"] = error_details
        await self.send_json(payload)

    @staticmethod
    def _extract_error_details(exc: ValidationError):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            return {
                str(key): [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value)]
                for key, value in detail.items()
            }
        if isinstance(detail, (list, tuple)):
            return {"detail": [str(item) for item in detail]}
        if detail:
            return {"detail": [str(detail)]}
        return None

    async def _handle_chat_send_message(self, content: dict):
        validated_data = await self._validate_ws_payload(
            ChatSendMessageWsSerializer,
            content,
            event="chat_send_message",
            invalid_message="消息参数非法",
        )
        if validated_data is None:
            return
        try:
            payload = await database_sync_to_async(execute_send_text_message_command)(
                self.scope["user"],
                validated_data["conversation_id"],
                content=validated_data.get("content", ""),
                client_message_id=validated_data.get("client_message_id"),
                quoted_message_id=validated_data.get("quoted_message_id"),
            )
        except ValidationError as exc:
            await self._send_error(
                event="chat_send_message",
                message=self._normalize_error(exc),
                error_kind="business",
                error_code="ws.business.chat_send_message.invalid",
                error_details=self._extract_error_details(exc),
            )
            return
        except PermissionDenied as exc:
            await self._send_error(
                event="chat_send_message",
                message=str(exc),
                error_kind="permission",
                error_code="ws.permission.chat_send_message.denied",
            )
            return

        await self._broadcast_chat_message(
            user_id=self.scope["user"].id,
            client_message_id=validated_data.get("client_message_id"),
            payload=payload,
        )

    async def _handle_chat_send_asset_message(self, content: dict):
        from chat.application.commands.attachments import execute_send_asset_message_command

        validated_data = await self._validate_ws_payload(
            ChatSendAssetMessageWsSerializer,
            content,
            event="chat_send_asset_message",
            invalid_message="会话或资产引用非法",
        )
        if validated_data is None:
            return
        try:
            payload = await database_sync_to_async(execute_send_asset_message_command)(
                self.scope["user"],
                validated_data["conversation_id"],
                source_asset_reference_id=validated_data["asset_reference_id"],
                quoted_message_id=validated_data.get("quoted_message_id"),
                emit_events=False,
            )
        except ValidationError as exc:
            await self._send_error(
                event="chat_send_asset_message",
                message=self._normalize_error(exc),
                error_kind="business",
                error_code="ws.business.chat_send_asset_message.invalid",
                error_details=self._extract_error_details(exc),
            )
            return
        except PermissionDenied as exc:
            await self._send_error(
                event="chat_send_asset_message",
                message=str(exc),
                error_kind="permission",
                error_code="ws.permission.chat_send_asset_message.denied",
            )
            return

        await self._broadcast_chat_message(
            user_id=self.scope["user"].id,
            client_message_id=validated_data.get("client_message_id"),
            payload=payload,
        )

    async def _handle_chat_mark_read(self, content: dict):
        validated_data = await self._validate_ws_payload(
            ChatMarkReadWsSerializer,
            content,
            event="chat_mark_read",
            invalid_message="会话或已读序号非法",
        )
        if validated_data is None:
            return
        try:
            payload = await database_sync_to_async(execute_mark_conversation_read_command)(
                self.scope["user"],
                validated_data["conversation_id"],
                last_read_sequence=validated_data["last_read_sequence"],
            )
        except ValidationError as exc:
            await self._send_error(
                event="chat_mark_read",
                message=self._normalize_error(exc),
                error_kind="business",
                error_code="ws.business.chat_mark_read.invalid",
                error_details=self._extract_error_details(exc),
            )
            return
        except PermissionDenied as exc:
            await self._send_error(
                event="chat_mark_read",
                message=str(exc),
                error_kind="permission",
                error_code="ws.permission.chat_mark_read.denied",
            )
            return

        await self._send_user_payload(
            self.scope["user"].id,
            build_ws_event(
                "chat.unread.updated",
                {
                    "conversation_id": payload["conversation_id"],
                    "unread_count": payload["unread_count"],
                    "total_unread_count": payload["total_unread_count"],
                    "last_read_sequence": payload["last_read_sequence"],
                },
            ),
        )

    async def _handle_chat_typing(self, content: dict):
        validated_data = await self._validate_ws_payload(
            ChatTypingWsSerializer,
            content,
            event="chat_typing",
            invalid_message="conversation_id 非法",
        )
        if validated_data is None:
            return
        try:
            payload = await database_sync_to_async(execute_chat_typing_query)(
                self.scope["user"],
                validated_data["conversation_id"],
                is_typing=validated_data.get("is_typing", False),
            )
        except ValidationError as exc:
            await self._send_error(
                event="chat_typing",
                message=self._normalize_error(exc),
                error_kind="business",
                error_code="ws.business.chat_typing.invalid",
                error_details=self._extract_error_details(exc),
            )
            return
        except PermissionDenied as exc:
            await self._send_error(
                event="chat_typing",
                message=str(exc),
                error_kind="permission",
                error_code="ws.permission.chat_typing.denied",
            )
            return

        for user_id in payload["target_user_ids"]:
            await self._send_user_payload(
                user_id,
                build_ws_event(
                    "chat.typing.updated",
                    {
                        "conversation_id": payload["conversation_id"],
                        "user": payload["user"],
                        "is_typing": payload["is_typing"],
                    },
                ),
            )

    @staticmethod
    def _normalize_error(exc: ValidationError) -> str:
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            first_value = next(iter(detail.values()), "请求参数非法")
            if isinstance(first_value, (list, tuple)):
                return str(first_value[0]) if first_value else "请求参数非法"
            return str(first_value)
        if isinstance(detail, (list, tuple)):
            return str(detail[0]) if detail else "请求参数非法"
        return str(detail or exc)

    async def disconnect(self, code):
        """处理 WebSocket 断开连接"""
        if self.user_group_name:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        for group_name in list(self.upload_task_groups):
            await self.channel_layer.group_discard(group_name, self.channel_name)
        self.upload_task_groups.clear()

    async def receive_json(self, content, **kwargs):
        """处理客户端消息"""
        message_type = str(content.get("type", "message")).strip()
        
        # 心跳 ping/pong
        if message_type == "ping":
            await self.send_json({"type": "pong", "timestamp": content.get("timestamp")})
            return

        if message_type == "chat_send_message":
            await self._handle_chat_send_message(content)
            return

        if message_type == "chat_send_asset_message":
            await self._handle_chat_send_asset_message(content)
            return

        if message_type == "chat_mark_read":
            await self._handle_chat_mark_read(content)
            return

        if message_type == "chat_typing":
            await self._handle_chat_typing(content)
            return

        # 订阅上传任务进度
        if message_type == "subscribe_upload_task":
            validated_data = await self._validate_ws_payload(
                UploadTaskSubscriptionWsSerializer,
                content,
                event="subscribe_upload_task",
                invalid_message="task_id 不能为空",
            )
            if validated_data is None:
                return
            task_id = validated_data["task_id"]

            group_name = f"upload_task_{task_id}"
            if group_name not in self.upload_task_groups:
                await self.channel_layer.group_add(group_name, self.channel_name)
                self.upload_task_groups.add(group_name)

            await self.send_json({"type": "upload_subscribed", "task_id": task_id})

            # 兜底：如果客户端订阅晚于任务完成，回放 Celery 结果，避免前端一直等待 done/failed。
            result = AsyncResult(task_id, app=celery_app)
            if result.successful():
                payload = result.result if isinstance(result.result, dict) else {}
                await self.send_json(
                    {
                        "type": "upload_progress",
                        "task_id": task_id,
                        "status": "done",
                        "progress": 100,
                        "message": str(payload.get("message", "合并完成")),
                        "relative_path": payload.get("relative_path", ""),
                        "url": payload.get("url", ""),
                    }
                )
                return

            if result.failed():
                failed_message = str(result.result) if result.result else "后台合并失败"
                await self.send_json(
                    {
                        "type": "upload_progress",
                        "task_id": task_id,
                        "status": "failed",
                        "progress": 100,
                        "message": failed_message,
                    }
                )
            return

        # 取消订阅上传任务进度
        if message_type == "unsubscribe_upload_task":
            validated_data = await self._validate_ws_payload(
                UploadTaskSubscriptionWsSerializer,
                content,
                event="unsubscribe_upload_task",
                invalid_message="task_id 不能为空",
            )
            if validated_data is None:
                return
            task_id = validated_data["task_id"]

            group_name = f"upload_task_{task_id}"
            if group_name in self.upload_task_groups:
                await self.channel_layer.group_discard(group_name, self.channel_name)
                self.upload_task_groups.remove(group_name)

            await self.send_json({"type": "upload_unsubscribed", "task_id": task_id})
            return

        # 回显消息
        validated_data = await self._validate_ws_payload(
            EchoWsSerializer,
            content,
            event="message",
            invalid_message="消息不能为空",
        )
        if validated_data is None:
            return
        await self.send_json({"type": "echo", "message": validated_data["message"]})

    async def upload_progress(self, event):
        """处理上传进度事件"""
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("type", "upload_progress")
        await self.send_json(payload)

    async def system_event(self, event):
        """处理系统事件（如强制下线）"""
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        await self.send_json(payload)
