from auth.permissions import AuthenticatedPermission as IsAuthenticated, ensure_request_permission
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from game.models import GameBestRecord
from game.serializers import GameBestRecordSerializer, SubmitBestRecordSerializer


def _resolve_limit(raw_value: str | None, default: int = 10, minimum: int = 1, maximum: int = 100) -> int:
	try:
		limit = int(raw_value or default)
	except (TypeError, ValueError):
		return default
	return max(minimum, min(maximum, limit))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_best_record_view(request):
	ensure_request_permission(request, "game.view_leaderboard")
	game_code = str(request.query_params.get("game_code", "")).strip().lower()
	if not game_code:
		return Response({"detail": "game_code 不能为空"}, status=status.HTTP_400_BAD_REQUEST)

	record = GameBestRecord.objects.filter(user=request.user, game_code=game_code).select_related("user").first()
	if record is None:
		return Response({"record": None})
	return Response({"record": GameBestRecordSerializer(record).data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def leaderboard_view(request):
	ensure_request_permission(request, "game.view_leaderboard")
	game_code = str(request.query_params.get("game_code", "")).strip().lower()
	if not game_code:
		return Response({"detail": "game_code 不能为空"}, status=status.HTTP_400_BAD_REQUEST)

	limit = _resolve_limit(request.query_params.get("limit"), default=10)
	queryset = GameBestRecord.objects.filter(game_code=game_code).select_related("user")[:limit]
	results = []
	for index, record in enumerate(queryset, start=1):
		payload = GameBestRecordSerializer(record).data
		payload["rank"] = index
		results.append(payload)

	return Response(
		{
			"game_code": game_code,
			"limit": limit,
			"results": results,
		}
	)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_best_record_view(request):
	ensure_request_permission(request, "game.submit_best_record")
	serializer = SubmitBestRecordSerializer(data=request.data)
	serializer.is_valid(raise_exception=True)
	validated = serializer.validated_data

	game_code = validated["game_code"]
	game_name = validated.get("game_name", "")
	score = validated["score"]
	board_snapshot = validated.get("board_snapshot", [])

	record = GameBestRecord.objects.filter(user=request.user, game_code=game_code).select_related("user").first()
	previous_best_score = record.best_score if record else 0
	updated = False

	if record is None:
		record = GameBestRecord.objects.create(
			user=request.user,
			game_code=game_code,
			game_name=game_name,
			best_score=score,
			board_snapshot=board_snapshot,
			finished_at=timezone.now(),
		)
		updated = True
	elif score > record.best_score:
		record.game_name = game_name or record.game_name
		record.best_score = score
		record.board_snapshot = board_snapshot
		record.finished_at = timezone.now()
		record.save(update_fields=["game_name", "best_score", "board_snapshot", "finished_at", "updated_at"])
		updated = True

	return Response(
		{
			"updated": updated,
			"previous_best_score": previous_best_score,
			"record": GameBestRecordSerializer(record).data,
		}
	)
