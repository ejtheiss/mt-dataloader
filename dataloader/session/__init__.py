"""Server-side session cache for the Dataloader application (``dataloader.session``).

Houses ``SessionState``, the in-memory session store, and the
``get_session`` helper (not yet wired as a FastAPI ``Depends`` everywhere).

**Process-local cache:** ``sessions`` is an in-memory ``dict`` (tokens are not
shared across workers). **Wave D:** durable continuity lives in SQLite
(``loader_drafts``); this module remains the **hot cache**. After restart, use
**Resume saved draft** on Setup (re-runs validate with stored config JSON; API
key comes from the sidebar, not the DB). Do not add Redis as a second store.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Request

from dataloader.engine import RefRegistry
from flow_compiler import FlowIR
from models import CreatedResourceRow, DataLoaderConfig
from org import DiscoveryResult, OrgRegistry, ReconciliationResult

SESSION_TTL_SECONDS = 600


@dataclass
class SessionState:
    """Cached state between validate and execute.

    **Authoring vs executable config**

    - ``authoring_config_json``: last validated JSON **before** the emit pass strips
      ``funds_flows`` and flattens flow steps into resource sections. Use this to
      re-run generation / find flow patterns (see ``_get_base_config`` in flows router).
    - ``config`` / ``config_json_text``: the **current executable** ``DataLoaderConfig``
      — same object the DAG, preview, and execute paths use. After **Apply scenario**,
      both are the merged generated load (Faker-filled resources, empty ``funds_flows``).
    - ``base_config_json``: snapshot from first successful validate in the session
      (historically the emitted text at that moment); generation prefers
      ``authoring_config_json`` when it still contains ``funds_flows``.
    - ``working_config_json``: Monaco / flows-page editor buffer; kept equal to
      ``config_json_text`` whenever the server updates ``session.config``. Edits only
      affect the live load after **Re-validate** (or another server merge); until then
      the buffer can diverge if the client edits without submitting.
    """

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
    update_refs: dict[str, str] = field(default_factory=dict)
    payload_overrides: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    flow_ir: list[FlowIR] | None = None
    expanded_flows: list | None = None
    pattern_flow_ir: list[FlowIR] | None = None
    pattern_expanded_flows: list | None = None
    base_config_json: str | None = None
    #: Snapshot of validated config **before** compile strips ``funds_flows`` (emit pass).
    #: Used for ``generate_from_recipe`` / ``_compose_all_recipes`` base lookup.
    authoring_config_json: str | None = None
    generation_recipes: dict[str, dict] = field(default_factory=dict)
    working_config_json: str | None = None
    mermaid_diagrams: list[str] | None = None
    view_data_cache: list | None = None
    source_file_path: str | None = None
    org_label: str | None = None
    #: Set only for cleanup SSE sessions — snapshot of created resources (reverse order).
    cleanup_resources: tuple[CreatedResourceRow, ...] | None = None
    cleanup_run_id: str | None = None
    #: Advisory ``validate_flow`` diagnostics (serialized for templates/log).
    flow_diagnostics: list[dict] | None = None


#: All active validate/execute/flow sessions for this process only.
sessions: dict[str, SessionState] = {}


def prune_expired_sessions() -> int:
    """Remove sessions older than SESSION_TTL_SECONDS. Returns count removed."""
    now = time.time()
    expired = [k for k, v in sessions.items() if now - v.created_at > SESSION_TTL_SECONDS]
    for k in expired:
        del sessions[k]
    return len(expired)


def get_session(request: Request) -> tuple[str, SessionState | None]:
    """Extract session token and state from request headers."""
    token = request.headers.get("x-session-token", "")
    return token, sessions.get(token)
