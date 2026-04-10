from __future__ import annotations

from dataloader.handlers.constants import SDK_ATTR_MAP
from dataloader.handlers.mt_client import MTClient


async def call(mt: MTClient, resource_type: str, resource_id: str) -> dict:
    """GET a single resource by type and ID."""
    sdk_attr = SDK_ATTR_MAP.get(resource_type)
    if not sdk_attr:
        raise ValueError(f"No SDK mapping for resource type '{resource_type}'")
    result = await getattr(mt.sdk, sdk_attr).retrieve(resource_id)
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)
