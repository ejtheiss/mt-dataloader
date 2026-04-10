from __future__ import annotations

from dataloader.handlers.constants import SDK_ATTR_MAP
from dataloader.handlers.mt_client import MTClient


async def call(mt: MTClient, resource_type: str, **filters) -> list[dict]:
    """List resources by type with optional filters."""
    sdk_attr = SDK_ATTR_MAP.get(resource_type)
    if not sdk_attr:
        raise ValueError(f"No SDK mapping for resource type '{resource_type}'")
    results = []
    async for item in getattr(mt.sdk, sdk_attr).list(**filters):
        results.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
        if len(results) >= 100:
            break
    return results
