from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from game.models import GameBestRecord
from user.models import Permission, Role, User


class GameApiTests(APITestCase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(
			username="game_player",
			password="Test123456",
			display_name="游戏玩家",
		)
		self.other_user = User.objects.create_user(
			username="game_peer",
			password="Test123456",
			display_name="对手玩家",
		)
		self.role, _ = Role.objects.get_or_create(
			name="游戏测试角色",
			defaults={"description": "游戏接口测试使用"},
		)
		self.client.force_authenticate(self.user)

	def _grant_permissions(self, *codes: str):
		permissions = Permission.objects.filter(code__in=codes)
		self.role.permissions.set(permissions)
		self.user.roles.add(self.role)

	def test_leaderboard_requires_permission(self):
		response = self.client.get("/api/game/leaderboard/", {"game_code": "2048"})

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_leaderboard_returns_ranked_results_with_limit_clamp(self):
		self._grant_permissions("game.view_leaderboard")
		GameBestRecord.objects.create(
			user=self.user,
			game_code="2048",
			game_name="2048",
			best_score=512,
			finished_at=timezone.now(),
		)
		GameBestRecord.objects.create(
			user=self.other_user,
			game_code="2048",
			game_name="2048",
			best_score=1024,
			finished_at=timezone.now(),
		)
		third_user = User.objects.create_user(username="game_third", password="Test123456", display_name="第三玩家")
		GameBestRecord.objects.create(
			user=third_user,
			game_code="2048",
			game_name="2048",
			best_score=256,
			finished_at=timezone.now(),
		)

		response = self.client.get("/api/game/leaderboard/", {"game_code": "2048", "limit": "200"})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		body = response.json()
		self.assertEqual(body["game_code"], "2048")
		self.assertEqual(body["limit"], 100)
		self.assertEqual([item["rank"] for item in body["results"]], [1, 2, 3])
		self.assertEqual([item["best_score"] for item in body["results"]], [1024, 512, 256])
		self.assertEqual(body["results"][0]["username"], "game_peer")

	def test_my_best_record_returns_null_without_existing_record(self):
		self._grant_permissions("game.view_leaderboard")

		response = self.client.get("/api/game/records/my-best/", {"game_code": "2048"})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.json(), {"record": None})

	def test_submit_best_record_requires_permission(self):
		response = self.client.post(
			"/api/game/records/submit-best/",
			{"game_code": "2048", "game_name": "2048", "score": 128, "board_snapshot": [[2, 4], [8, 16]]},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_submit_best_record_creates_and_only_updates_when_score_improves(self):
		self._grant_permissions("game.submit_best_record", "game.view_leaderboard")

		create_response = self.client.post(
			"/api/game/records/submit-best/",
			{"game_code": " 2048 ", "game_name": "2048", "score": 128, "board_snapshot": [[2, 4], [8, 16]]},
			format="json",
		)

		self.assertEqual(create_response.status_code, status.HTTP_200_OK)
		create_body = create_response.json()
		self.assertTrue(create_body["updated"])
		self.assertEqual(create_body["previous_best_score"], 0)
		self.assertEqual(create_body["record"]["game_code"], "2048")
		self.assertEqual(create_body["record"]["best_score"], 128)

		lower_response = self.client.post(
			"/api/game/records/submit-best/",
			{"game_code": "2048", "game_name": "2048", "score": 64, "board_snapshot": [[0, 2], [4, 8]]},
			format="json",
		)

		self.assertEqual(lower_response.status_code, status.HTTP_200_OK)
		lower_body = lower_response.json()
		self.assertFalse(lower_body["updated"])
		self.assertEqual(lower_body["previous_best_score"], 128)
		self.assertEqual(lower_body["record"]["best_score"], 128)

		higher_response = self.client.post(
			"/api/game/records/submit-best/",
			{"game_code": "2048", "game_name": "2048 冲榜", "score": 256, "board_snapshot": [[16, 32], [64, 128]]},
			format="json",
		)

		self.assertEqual(higher_response.status_code, status.HTTP_200_OK)
		higher_body = higher_response.json()
		self.assertTrue(higher_body["updated"])
		self.assertEqual(higher_body["previous_best_score"], 128)
		self.assertEqual(higher_body["record"]["best_score"], 256)
		self.assertEqual(higher_body["record"]["game_name"], "2048 冲榜")

		record = GameBestRecord.objects.get(user=self.user, game_code="2048")
		self.assertEqual(record.best_score, 256)
		self.assertEqual(record.board_snapshot, [[16, 32], [64, 128]])
