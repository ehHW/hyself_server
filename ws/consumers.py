"""
WebSocket 消费者 - 处理客户端连接和消息
"""
from celery.result import AsyncResult
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from bbot_server.celery import app as celery_app


class GlobalWebSocketConsumer(AsyncJsonWebsocketConsumer):
    """
    全局 WebSocket 消费者，处理：
    - 用户连接/断开连接
    - ping/pong 心跳
    - 上传任务订阅管理
    - 系统事件推送（如强制下线）
    """
    
    async def connect(self):
        """处理 WebSocket 连接"""
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
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

    async def disconnect(self, code):
        """处理 WebSocket 断开连接"""
        if hasattr(self, "user_group_name"):
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

        # 订阅上传任务进度
        if message_type == "subscribe_upload_task":
            task_id = str(content.get("task_id", "")).strip()
            if not task_id:
                await self.send_json({"type": "error", "message": "task_id 不能为空"})
                return

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
            task_id = str(content.get("task_id", "")).strip()
            if not task_id:
                await self.send_json({"type": "error", "message": "task_id 不能为空"})
                return

            group_name = f"upload_task_{task_id}"
            if group_name in self.upload_task_groups:
                await self.channel_layer.group_discard(group_name, self.channel_name)
                self.upload_task_groups.remove(group_name)

            await self.send_json({"type": "upload_unsubscribed", "task_id": task_id})
            return

        # 回显消息
        text = str(content.get("message", "")).strip()
        if not text:
            await self.send_json({"type": "error", "message": "消息不能为空"})
            return
        await self.send_json({"type": "echo", "message": text})

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
