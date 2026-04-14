from __future__ import annotations

from hyself.models import AssetReference


def get_asset_reference_with_asset(reference_id: int):
    return AssetReference.objects.select_related("asset").filter(id=reference_id).first()