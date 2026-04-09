"""Tag-filtered OpenAPI document for agents and codegen (Plan 06)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head", "trace"})

AGENT_OPENAPI_TAGS = frozenset({"agent"})


def filter_openapi_for_agent(schema: dict[str, Any]) -> dict[str, Any]:
    paths = schema.get("paths") or {}
    out_paths: dict[str, Any] = {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        kept_methods: dict[str, Any] = {}
        passthrough: dict[str, Any] = {}
        for key, sub in item.items():
            if key in HTTP_METHODS:
                if isinstance(sub, dict):
                    tags = sub.get("tags") or []
                    if AGENT_OPENAPI_TAGS.intersection(tags):
                        kept_methods[key] = sub
            else:
                passthrough[key] = sub
        if not kept_methods:
            continue
        out_paths[path] = {**passthrough, **kept_methods}
    out = dict(schema)
    out["paths"] = out_paths
    info = dict(out.get("info") or {})
    base_title = info.get("title") or "API"
    info["title"] = f"{base_title} (agent)"
    desc = (info.get("description") or "").strip()
    suffix = "Filtered to operations tagged `agent` for tools and codegen."
    info["description"] = f"{desc}\n\n{suffix}".strip() if desc else suffix
    out["info"] = info
    return out


def build_agent_openapi_schema(app: FastAPI) -> dict[str, Any]:
    base = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    return filter_openapi_for_agent(base)
