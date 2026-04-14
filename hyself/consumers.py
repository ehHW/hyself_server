from channels.generic.websocket import AsyncJsonWebsocketConsumer


class UploadProgressConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
            return

        self.task_id = self.scope["url_route"]["kwargs"].get("task_id", "")
        if not self.task_id:
            await self.close(code=4400)
            return

        self.group_name = f"upload_task_{self.task_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "type": "upload_progress",
                "status": "connected",
                "task_id": self.task_id,
                "message": "已订阅上传任务进度",
            }
        )

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if str(content.get("type", "")).strip() == "ping":
            await self.send_json({"type": "pong", "timestamp": content.get("timestamp")})

    async def upload_progress(self, event):
        await self.send_json(event.get("payload", {}))
