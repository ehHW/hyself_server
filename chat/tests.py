import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.test import APIClient

from bbot.asset_compat import ensure_asset_reference_for_uploaded_file
from bbot.models import AssetReference, UploadedFile
from bbot_server.asgi import application
from bbot.tasks import merge_large_file_task
from chat.domain.access import get_conversation_access
from chat.models import ChatConversation, ChatConversationMember, ChatMessage
from chat.models import ChatFriendship, ChatGroupConfig, ChatGroupJoinRequest, build_pair_key
from user.models import User
from utils.upload import get_temp_root


class WebsocketCommunicator(ApplicationCommunicator):
	def __init__(self, application, path, headers=None, subprotocols=None, spec_version=None):
		parsed = urlparse(path)
		scope = {
			"type": "websocket",
			"path": unquote(parsed.path),
			"query_string": parsed.query.encode("utf-8"),
			"headers": headers or [],
			"subprotocols": subprotocols or [],
		}
		if spec_version:
			scope["spec_version"] = spec_version
		super().__init__(application, scope)
		self.response_headers = None

	async def connect(self, timeout=1):
		await self.send_input({"type": "websocket.connect"})
		response = await self.receive_output(timeout)
		if response["type"] == "websocket.close":
			return False, response.get("code", 1000)
		self.response_headers = response.get("headers", [])
		return True, response.get("subprotocol", None)

	async def send_json_to(self, data):
		await self.send_input({"type": "websocket.receive", "text": json.dumps(data)})

	async def receive_json_from(self, timeout=1):
		response = await self.receive_output(timeout)
		assert response["type"] == "websocket.send"
		return json.loads(response["text"])

	async def disconnect(self, code=1000, timeout=1):
		await self.send_input({"type": "websocket.disconnect", "code": code})
		await self.wait(timeout)


@override_settings(MEDIA_URL="/media/")
class ChatAttachmentMessageTests(TestCase):
	def setUp(self):
		super().setUp()
		self._temp_media_dir = tempfile.TemporaryDirectory()
		self.override = override_settings(MEDIA_ROOT=self._temp_media_dir.name)
		self.override.enable()
		self.client = APIClient()
		self.user = User.objects.create_user(username="chat_sender", password="Test123456")
		self.friend = User.objects.create_user(username="chat_receiver", password="Test123456")
		self.third_user = User.objects.create_user(username="chat_third", password="Test123456")
		self.client.force_authenticate(self.user)
		self.conversation = ChatConversation.objects.create(
			type=ChatConversation.Type.DIRECT,
			status=ChatConversation.Status.ACTIVE,
			direct_pair_key="pair_demo",
		)
		ChatConversationMember.objects.create(
			conversation=self.conversation,
			user=self.user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		ChatConversationMember.objects.create(
			conversation=self.conversation,
			user=self.friend,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		self.group_conversation = ChatConversation.objects.create(
			type=ChatConversation.Type.GROUP,
			status=ChatConversation.Status.ACTIVE,
			name="测试群聊",
			owner=self.user,
		)
		ChatGroupConfig.objects.create(
			conversation=self.group_conversation,
			join_approval_required=True,
			allow_member_invite=True,
		)
		ChatConversationMember.objects.create(
			conversation=self.group_conversation,
			user=self.user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.OWNER,
			show_in_list=True,
		)
		ChatConversationMember.objects.create(
			conversation=self.group_conversation,
			user=self.third_user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.ADMIN,
			show_in_list=True,
		)

	def tearDown(self):
		self.override.disable()
		self._temp_media_dir.cleanup()
		super().tearDown()

	def _create_active_friendship(self):
		return ChatFriendship.objects.create(
			pair_key=build_pair_key(self.user.id, self.friend.id),
			user_low=self.user if self.user.id < self.friend.id else self.friend,
			user_high=self.friend if self.user.id < self.friend.id else self.user,
			status=ChatFriendship.Status.ACTIVE,
		)

	def test_send_attachment_message_from_asset_reference(self):
		self._create_active_friendship()
		uploaded = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="diagram.png",
			stored_name="diagram_stored.png",
			relative_path="users/chat_sender_1/diagram_stored.png",
			file_size=321,
			file_md5="b" * 32,
		)
		source_reference = ensure_asset_reference_for_uploaded_file(uploaded)

		response = self.client.post(
			f"/api/chat/conversations/{self.conversation.id}/attachments/",
			{"asset_reference_id": source_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, 201)
		body = response.json()
		self.assertEqual(body["message"]["message_type"], ChatMessage.MessageType.IMAGE)
		self.assertEqual(body["message"]["payload"]["asset_reference_id"], body["asset_reference"]["id"])
		self.assertEqual(body["message"]["payload"]["source_asset_reference_id"], source_reference.id)
		self.assertTrue(
			AssetReference.objects.filter(
				id=body["asset_reference"]["id"],
				ref_domain=AssetReference.RefDomain.CHAT,
				ref_type=AssetReference.RefType.CHAT_ATTACHMENT,
			).exists()
		)

	def test_send_attachment_message_rejected_for_non_friend_direct_conversation(self):
		uploaded = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="restricted.pdf",
			stored_name="restricted.pdf",
			relative_path="users/chat_sender_1/restricted.pdf",
			file_size=128,
			file_md5="c" * 32,
		)
		source_reference = ensure_asset_reference_for_uploaded_file(uploaded)

		response = self.client.post(
			f"/api/chat/conversations/{self.conversation.id}/attachments/",
			{"asset_reference_id": source_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, 403)
		self.assertEqual(response.json()["detail"], "你们还不是好友，当前私聊暂不支持发送附件")

	def test_send_attachment_message_allowed_for_self_direct_conversation(self):
		self_conversation = ChatConversation.objects.create(
			type=ChatConversation.Type.DIRECT,
			status=ChatConversation.Status.ACTIVE,
			direct_pair_key=build_pair_key(self.user.id, self.user.id),
		)
		ChatConversationMember.objects.create(
			conversation=self_conversation,
			user=self.user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		uploaded = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="self-note.txt",
			stored_name="self-note.txt",
			relative_path="users/chat_sender_1/self-note.txt",
			file_size=64,
			file_md5="f" * 32,
		)
		source_reference = ensure_asset_reference_for_uploaded_file(uploaded)

		response = self.client.post(
			f"/api/chat/conversations/{self_conversation.id}/attachments/",
			{"asset_reference_id": source_reference.id},
			format="json",
		)

		self.assertEqual(response.status_code, 201)
		self.assertEqual(response.json()["message"]["payload"]["source_asset_reference_id"], source_reference.id)

	def test_chunk_merge_result_can_send_attachment_immediately(self):
		self._create_active_friendship()
		content = b"chunk-attachment-" * 256
		file_md5 = hashlib.md5(content).hexdigest()
		temp_dir = get_temp_root() / f"{self.user.id}_{file_md5}"
		temp_dir.mkdir(parents=True, exist_ok=True)
		chunks = [content[:1024], content[1024:2048], content[2048:]]
		for index, chunk in enumerate(chunks, start=1):
			(temp_dir / str(index)).write_bytes(chunk)

		merge_result = merge_large_file_task.apply(
			kwargs={
				"file_md5": file_md5,
				"total_chunks": len(chunks),
				"file_name": "merged-report.pdf",
				"display_name": "merged-report.pdf",
				"total_md5": file_md5,
				"file_size": len(content),
				"user_id": self.user.id,
				"parent_id": None,
			}
		).get()

		self.assertEqual(merge_result["status"], "done")
		asset_reference_id = merge_result.get("asset_reference_id")
		self.assertTrue(asset_reference_id)

		response = self.client.post(
			f"/api/chat/conversations/{self.conversation.id}/attachments/",
			{"asset_reference_id": asset_reference_id},
			format="json",
		)

		self.assertEqual(response.status_code, 201)
		body = response.json()
		self.assertEqual(body["message"]["message_type"], ChatMessage.MessageType.FILE)
		self.assertEqual(body["message"]["payload"]["source_asset_reference_id"], asset_reference_id)

	def test_forward_messages_api_copies_text_message_to_target_conversation(self):
		self._create_active_friendship()
		target_conversation = ChatConversation.objects.create(
			type=ChatConversation.Type.GROUP,
			status=ChatConversation.Status.ACTIVE,
			name="转发目标",
			owner=self.user,
		)
		ChatConversationMember.objects.create(
			conversation=target_conversation,
			user=self.user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.OWNER,
			show_in_list=True,
		)
		ChatConversationMember.objects.create(
			conversation=target_conversation,
			user=self.friend,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)

		source_message = ChatMessage.objects.create(
			conversation=self.conversation,
			sequence=1,
			sender=self.user,
			message_type=ChatMessage.MessageType.TEXT,
			content="原始文本消息",
		)

		response = self.client.post(
			"/api/chat/messages/forward/",
			{
				"target_conversation_id": target_conversation.id,
				"message_ids": [source_message.id],
			},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["forwarded_count"], 1)
		forwarded_message = ChatMessage.objects.filter(conversation=target_conversation).exclude(id=source_message.id).get()
		self.assertEqual(forwarded_message.content, "原始文本消息")
		self.assertEqual(forwarded_message.sender_id, self.user.id)
		self.assertEqual(forwarded_message.payload["forwarded_from_message"]["id"], source_message.id)

	def test_invite_member_api_sends_group_invitation_message(self):
		response = self.client.post(
			f"/api/chat/conversations/{self.group_conversation.id}/members/invite/",
			{"target_user_id": self.friend.id},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		body = response.json()
		self.assertEqual(body["mode"], "message_sent")
		direct_message = ChatMessage.objects.get(id=body["message"]["id"])
		self.assertEqual(direct_message.content, "邀请你加入群聊")
		self.assertEqual(direct_message.payload["group_invitation"]["conversation_id"], self.group_conversation.id)
		self.assertEqual(direct_message.payload["group_invitation"]["inviter"]["id"], self.user.id)

	def test_apply_group_invitation_creates_pending_join_request(self):
		invite_response = self.client.post(
			f"/api/chat/conversations/{self.group_conversation.id}/members/invite/",
			{"target_user_id": self.friend.id},
			format="json",
		)
		self.assertEqual(invite_response.status_code, 200)

		self.client.force_authenticate(self.friend)
		response = self.client.post(
			"/api/chat/group-invitations/apply/",
			{"conversation_id": self.group_conversation.id, "inviter_user_id": self.user.id},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		body = response.json()
		self.assertEqual(body["mode"], "pending_approval")
		join_request = ChatGroupJoinRequest.objects.get(conversation=self.group_conversation, target_user=self.friend)
		self.assertEqual(join_request.request_type, ChatGroupJoinRequest.RequestType.APPLICATION)
		self.assertEqual(join_request.status, ChatGroupJoinRequest.Status.PENDING)

	def test_handle_group_join_request_returns_serializable_payload(self):
		join_request = ChatGroupJoinRequest.objects.create(
			conversation=self.group_conversation,
			request_type=ChatGroupJoinRequest.RequestType.APPLICATION,
			inviter=self.friend,
			target_user=self.friend,
			status=ChatGroupJoinRequest.Status.PENDING,
		)
		self.client.force_authenticate(self.user)
		response = self.client.post(
			f"/api/chat/group-join-requests/{join_request.id}/handle/",
			{"action": "approve"},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		join_request.refresh_from_db()
		self.assertEqual(join_request.status, ChatGroupJoinRequest.Status.APPROVED)
		self.assertTrue(
			ChatConversationMember.objects.filter(
				conversation=self.group_conversation,
				user=self.friend,
				status=ChatConversationMember.Status.ACTIVE,
			).exists()
		)

	def test_handle_group_join_request_notifies_admins_to_refresh_pending_list(self):
		join_request = ChatGroupJoinRequest.objects.create(
			conversation=self.group_conversation,
			request_type=ChatGroupJoinRequest.RequestType.APPLICATION,
			inviter=self.friend,
			target_user=self.friend,
			status=ChatGroupJoinRequest.Status.PENDING,
		)
		self.client.force_authenticate(self.user)
		with patch("chat.application.commands.group_management.notify_chat_group_join_request_updated") as notify_mock:
			response = self.client.post(
				f"/api/chat/group-join-requests/{join_request.id}/handle/",
				{"action": "approve"},
				format="json",
			)

		self.assertEqual(response.status_code, 200)
		notified_user_ids = {call.args[0] for call in notify_mock.call_args_list}
		self.assertIn(self.user.id, notified_user_ids)
		self.assertIn(self.third_user.id, notified_user_ids)
		self.assertIn(self.friend.id, notified_user_ids)

	def test_leave_group_conversation_does_not_emit_system_notice(self):
		with patch("chat.application.commands.group_management.notify_chat_system_notice") as notify_mock:
			response = self.client.post(
				f"/api/chat/conversations/{self.group_conversation.id}/leave/",
				format="json",
			)

		self.assertEqual(response.status_code, 200)
		notify_mock.assert_not_called()

	def test_mute_member_with_zero_minutes_clears_mute(self):
		member = ChatConversationMember.objects.get(conversation=self.group_conversation, user=self.third_user)
		member.mute_until = member.joined_at
		member.mute_reason = "临时禁言"
		member.save(update_fields=["mute_until", "mute_reason", "updated_at"])

		response = self.client.post(
			f"/api/chat/conversations/{self.group_conversation.id}/members/{self.third_user.id}/mute/",
			{"mute_minutes": 0},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		member.refresh_from_db()
		self.assertIsNone(member.mute_until)
		self.assertEqual(member.mute_reason, "")

	def test_mute_all_only_owner_can_send_message(self):
		friend_member = ChatConversationMember.objects.create(
			conversation=self.group_conversation,
			user=self.friend,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		self.group_conversation.group_config.mute_all = True
		self.group_conversation.group_config.save(update_fields=["mute_all", "updated_at"])

		owner_access = get_conversation_access(self.user, self.group_conversation)
		admin_access = get_conversation_access(self.third_user, self.group_conversation)
		member_access = get_conversation_access(self.friend, self.group_conversation)

		self.assertTrue(owner_access.can_send_message)
		self.assertFalse(admin_access.can_send_message)
		self.assertFalse(member_access.can_send_message)


@override_settings(
	MEDIA_URL="/media/",
	CHANNEL_LAYERS={
		"default": {
			"BACKEND": "channels.layers.InMemoryChannelLayer",
		}
	},
)
class ChatAttachmentRealtimeWsTests(TransactionTestCase):
	def setUp(self):
		super().setUp()
		self._temp_media_dir = tempfile.TemporaryDirectory()
		self.override = override_settings(MEDIA_ROOT=self._temp_media_dir.name)
		self.override.enable()
		self.user = User.objects.create_user(username="ws_sender", password="Test123456")
		self.friend = User.objects.create_user(username="ws_receiver", password="Test123456")
		self.conversation = ChatConversation.objects.create(
			type=ChatConversation.Type.DIRECT,
			status=ChatConversation.Status.ACTIVE,
			direct_pair_key="pair_ws_demo",
		)
		ChatConversationMember.objects.create(
			conversation=self.conversation,
			user=self.user,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		ChatConversationMember.objects.create(
			conversation=self.conversation,
			user=self.friend,
			status=ChatConversationMember.Status.ACTIVE,
			role=ChatConversationMember.Role.MEMBER,
			show_in_list=True,
		)
		ChatFriendship.objects.create(
			pair_key=build_pair_key(self.user.id, self.friend.id),
			user_low=self.user if self.user.id < self.friend.id else self.friend,
			user_high=self.friend if self.user.id < self.friend.id else self.user,
			status=ChatFriendship.Status.ACTIVE,
		)
		self.friendship_pair_key = build_pair_key(self.user.id, self.friend.id)

	def tearDown(self):
		self.override.disable()
		self._temp_media_dir.cleanup()
		super().tearDown()

	async def _connect_ws(self, user):
		token = str(RefreshToken.for_user(user).access_token)
		communicator = WebsocketCommunicator(application, f"/ws/global/?token={token}")
		connected, _ = await communicator.connect()
		self.assertTrue(connected)
		welcome = await communicator.receive_json_from(timeout=3)
		self.assertEqual(welcome["type"], "system")
		return communicator

	async def _send_attachment_over_ws(self, asset_reference_id: int):
		sender_ws = await self._connect_ws(self.user)
		receiver_ws = await self._connect_ws(self.friend)
		client_message_id = "ws_asset_msg_1"
		try:
			await sender_ws.send_json_to(
				{
					"type": "chat_send_asset_message",
					"conversation_id": self.conversation.id,
					"asset_reference_id": asset_reference_id,
					"client_message_id": client_message_id,
				}
			)
			sender_ack = await sender_ws.receive_json_from(timeout=3)
			receiver_message = await receiver_ws.receive_json_from(timeout=3)
			receiver_conversation = await receiver_ws.receive_json_from(timeout=3)
			receiver_unread = await receiver_ws.receive_json_from(timeout=3)
			return sender_ack, receiver_message, receiver_conversation, receiver_unread
		finally:
			await sender_ws.disconnect()
			await receiver_ws.disconnect()

	def test_ws_attachment_send_returns_ack_and_receiver_events(self):
		uploaded = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="ws-diagram.png",
			stored_name="ws-diagram-stored.png",
			relative_path="users/ws_sender_1/ws-diagram-stored.png",
			file_size=256,
			file_md5="d" * 32,
		)
		source_reference = ensure_asset_reference_for_uploaded_file(uploaded)

		sender_ack, receiver_message, receiver_conversation, receiver_unread = async_to_sync(self._send_attachment_over_ws)(source_reference.id)

		self.assertEqual(sender_ack["type"], "event")
		self.assertEqual(sender_ack["event_type"], "chat.message.ack")
		self.assertEqual(sender_ack["payload"]["client_message_id"], "ws_asset_msg_1")
		self.assertEqual(sender_ack["payload"]["conversation_id"], self.conversation.id)
		self.assertEqual(sender_ack["payload"]["message"]["message_type"], ChatMessage.MessageType.IMAGE)
		self.assertEqual(sender_ack["payload"]["message"]["payload"]["source_asset_reference_id"], source_reference.id)

		self.assertEqual(receiver_message["type"], "event")
		self.assertEqual(receiver_message["event_type"], "chat.message.created")
		self.assertEqual(receiver_message["payload"]["conversation_id"], self.conversation.id)
		self.assertEqual(receiver_message["payload"]["message"]["payload"]["source_asset_reference_id"], source_reference.id)

		self.assertEqual(receiver_conversation["type"], "event")
		self.assertEqual(receiver_conversation["event_type"], "chat.conversation.updated")
		self.assertEqual(receiver_conversation["payload"]["conversation"]["id"], self.conversation.id)

		self.assertEqual(receiver_unread["type"], "event")
		self.assertEqual(receiver_unread["event_type"], "chat.unread.updated")
		self.assertEqual(receiver_unread["payload"]["conversation_id"], self.conversation.id)
		self.assertEqual(receiver_unread["payload"]["unread_count"], 1)

		message = ChatMessage.objects.get(conversation=self.conversation)
		self.assertEqual(message.message_type, ChatMessage.MessageType.IMAGE)
		self.assertEqual(message.payload["source_asset_reference_id"], source_reference.id)

	def test_ws_attachment_send_returns_error_for_non_friend_direct_conversation(self):
		ChatFriendship.objects.filter(pair_key=self.friendship_pair_key).delete()
		uploaded = UploadedFile.objects.create(
			created_by=self.user,
			parent=None,
			is_dir=False,
			display_name="ws-blocked.pdf",
			stored_name="ws-blocked.pdf",
			relative_path="users/ws_sender_1/ws-blocked.pdf",
			file_size=512,
			file_md5="e" * 32,
		)
		source_reference = ensure_asset_reference_for_uploaded_file(uploaded)

		async def scenario():
			sender_ws = await self._connect_ws(self.user)
			try:
				await sender_ws.send_json_to(
					{
						"type": "chat_send_asset_message",
						"conversation_id": self.conversation.id,
						"asset_reference_id": source_reference.id,
						"client_message_id": "ws_asset_msg_error_1",
					}
				)
				return await sender_ws.receive_json_from(timeout=3)
			finally:
				await sender_ws.disconnect()

		error_event = async_to_sync(scenario)()

		self.assertEqual(error_event["type"], "error")
		self.assertEqual(error_event["event"], "chat_send_asset_message")
		self.assertEqual(error_event["message"], "你们还不是好友，当前私聊暂不支持发送附件")
		self.assertFalse(ChatMessage.objects.filter(conversation=self.conversation).exists())

	def test_ws_text_message_can_include_reply_payload(self):
		quoted_message = ChatMessage.objects.create(
			conversation=self.conversation,
			sequence=1,
			sender=self.friend,
			message_type=ChatMessage.MessageType.TEXT,
			content="被引用的原消息",
		)

		async def scenario():
			sender_ws = await self._connect_ws(self.user)
			try:
				await sender_ws.send_json_to(
					{
						"type": "chat_send_message",
						"conversation_id": self.conversation.id,
						"content": "回复内容",
						"client_message_id": "ws_reply_msg_1",
						"quoted_message_id": quoted_message.id,
					}
				)
				return await sender_ws.receive_json_from(timeout=3)
			finally:
				await sender_ws.disconnect()

		sender_ack = async_to_sync(scenario)()

		self.assertEqual(sender_ack["type"], "event")
		self.assertEqual(sender_ack["event_type"], "chat.message.ack")
		self.assertEqual(sender_ack["payload"]["message"]["payload"]["reply_to_message"]["id"], quoted_message.id)
		self.assertEqual(sender_ack["payload"]["message"]["payload"]["reply_to_message"]["content_preview"], "被引用的原消息")
