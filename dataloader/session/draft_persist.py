"""Serialize ``SessionState`` ↔ ``LoaderDraft`` and upsert to SQLite (Wave D)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from loguru import logger

from dataloader.actor_library_runtime import ensure_actor_library_hydrated_from_legacy
from dataloader.engine import all_resources, dry_run, typed_ref_for
from dataloader.helpers import build_preview
from dataloader.routers.deps import get_current_app_user
from dataloader.session import SessionState
from db.repositories import loader_drafts as drafts_repo
from db.repositories.runs import RunAccessContext
from models.actor_library import LibraryActorEntry
from models.loader_draft import LoaderDraft


def loader_draft_from_session(session: SessionState) -> LoaderDraft:
    """Build a storable draft (no ``api_key`` / ``session_token``)."""
    return LoaderDraft(
        org_id=session.org_id,
        org_label=session.org_label,
        config_json_text=session.config_json_text,
        batches=session.batches,
        preview_items=list(session.preview_items),
        base_config_json=session.base_config_json,
        authoring_config_json=session.authoring_config_json,
        working_config_json=session.working_config_json,
        generation_recipes={k: dict(v) for k, v in session.generation_recipes.items()},
        actor_library=[
            LibraryActorEntry.model_validate(dict(x)) for x in (session.actor_library or [])
        ],
        actor_bindings={k: dict(v) for k, v in (session.actor_bindings or {}).items()},
        mermaid_diagrams=list(session.mermaid_diagrams or []),
        source_file_path=session.source_file_path,
        flow_diagnostics=list(session.flow_diagnostics) if session.flow_diagnostics else None,
        skip_refs=sorted(session.skip_refs),
        update_refs=dict(session.update_refs),
        payload_overrides=sorted(session.payload_overrides),
    )


def merge_loader_draft_into_session(session: SessionState, draft: LoaderDraft) -> None:
    """After a fresh validate pipeline, overlay durable fields and refresh DAG preview.

    Re-runs ``dry_run`` / ``build_preview`` so ``skip_refs`` / ``update_refs`` from
    the draft stay consistent with ``session.config``.
    """
    if draft.generation_recipes:
        session.generation_recipes = {k: dict(v) for k, v in draft.generation_recipes.items()}
    session.actor_library = [
        e.model_dump(exclude_none=True) for e in (draft.actor_library or [])
    ]
    session.actor_bindings = {k: dict(v) for k, v in (draft.actor_bindings or {}).items()}
    ensure_actor_library_hydrated_from_legacy(session)
    if draft.working_config_json and draft.working_config_json.strip():
        session.working_config_json = draft.working_config_json
    if draft.source_file_path:
        session.source_file_path = draft.source_file_path
    if draft.org_label:
        session.org_label = draft.org_label
    if draft.base_config_json and draft.base_config_json.strip():
        session.base_config_json = draft.base_config_json
    if draft.authoring_config_json and draft.authoring_config_json.strip():
        session.authoring_config_json = draft.authoring_config_json

    session.skip_refs = set(draft.skip_refs)
    session.update_refs = dict(draft.update_refs)
    session.payload_overrides = set(draft.payload_overrides)

    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    session.batches = dry_run(session.config, known, skip_refs=session.skip_refs)
    resource_map = {typed_ref_for(r): r for r in all_resources(session.config)}
    session.preview_items = build_preview(
        session.batches,
        resource_map,
        skip_refs=session.skip_refs,
        reconciliation=session.reconciliation,
        update_refs=session.update_refs,
    )


def run_access_context_for_request(request: Request) -> RunAccessContext:
    """Map ``CurrentAppUser`` (stub or future auth) to repository visibility rules."""
    u = get_current_app_user(request)
    return RunAccessContext(user_id=u.id, is_admin=u.is_admin)


async def persist_loader_draft(request: Request, session: SessionState) -> None:
    """Best-effort upsert; no-op if DB session factory is unavailable."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return
    ctx = run_access_context_for_request(request)
    try:
        draft = loader_draft_from_session(session)
        ts = datetime.now(timezone.utc).isoformat()
        async with factory() as db:
            await drafts_repo.upsert_loader_draft(
                db,
                user_id=ctx.user_id,
                ctx=ctx,
                draft=draft,
                updated_at=ts,
            )
            await db.commit()
    except Exception as exc:
        logger.warning("loader draft persist failed (non-fatal): {}", exc)


LOADER_DRAFT_RETENTION_DAYS = 30
