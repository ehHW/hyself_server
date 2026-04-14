from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from chat.domain.access import get_conversation_access
from chat.models import ChatConversation
from hyself.models import AssetReference
from hyself.validators import parse_parent_id


def resolve_upload_permission_code(category: str) -> str | None:
    if category == "profile":
        return None
    if category == "chat":
        return "chat.send_attachment"
    return "file.upload_file"


def ensure_reference_can_be_saved_to_resource(user, source_reference: AssetReference) -> None:
    if source_reference.owner_user_id in {None, user.id}:
        return
    if source_reference.ref_domain != AssetReference.RefDomain.CHAT or source_reference.ref_type != AssetReference.RefType.CHAT_ATTACHMENT:
        raise PermissionDenied("当前无权保存该附件")

    conversation_id = parse_parent_id(source_reference.ref_object_id)
    if conversation_id is None:
        raise PermissionDenied("当前无权保存该附件")

    conversation = ChatConversation.objects.filter(
        id=conversation_id,
        deleted_at__isnull=True,
        status=ChatConversation.Status.ACTIVE,
    ).first()
    if conversation is None:
        raise PermissionDenied("当前无权保存该附件")

    get_conversation_access(user, conversation)