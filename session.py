"""Server-side session cache for the Dataloader application.

Houses ``SessionState``, the in-memory session store, and the
``get_session`` dependency for FastAPI route handlers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Request

from engine import RefRegistry
from flow_compiler import FlowIR
from models import DataLoaderConfig
from org import DiscoveryResult, OrgRegistry, ReconciliationResult

SESSION_TTL_SECONDS = 600


@dataclass
class SessionState:
    """Cached state between validate and execute."""

    session_token: str
    api_key: str
    org_id: str
    config: DataLoaderConfig
    config_json_text: str
    registry: RefRegistry
    batches: list[list[str]]
    preview_items: list[dict] = field(default_factory=list)
    org_registry: OrgRegistry | None = None
    discovery: DiscoveryResult | None = None
    reconciliation: ReconciliationResult | None = None
    skip_refs: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    flow_ir: list[FlowIR] | None = None
    expanded_flows: list | None = None
    pattern_flow_ir: list[FlowIR] | None = None
    pattern_expanded_flows: list | None = None
    base_config_json: str | None = None
    generation_recipes: dict[str, dict] = field(default_factory=dict)
    working_config_json: str | None = None
    mermaid_diagrams: list[str] | None = None
    view_data_cache: list | None = None


sessions: dict[str, SessionState] = {}


def prune_expired_sessions() -> int:
    """Remove sessions older than SESSION_TTL_SECONDS. Returns count removed."""
    now = time.time()
    expired = [
        k for k, v in sessions.items() if now - v.created_at > SESSION_TTL_SECONDS
    ]
    for k in expired:
        del sessions[k]
    return len(expired)


def get_session(request: Request) -> tuple[str, SessionState | None]:
    """Extract session token and state from request headers."""
    token = request.headers.get("x-session-token", "")
    return token, sessions.get(token)
