"""Wave D: ``SessionState`` ↔ ``LoaderDraft`` helpers (no DB)."""

from __future__ import annotations

from dataloader.engine import RefRegistry
from dataloader.session import SessionState
from dataloader.session.draft_persist import (
    loader_draft_from_session,
    merge_loader_draft_into_session,
)
from models import DataLoaderConfig
from models.loader_draft import LoaderDraft


def test_loader_draft_from_session_excludes_ephemeral() -> None:
    s = SessionState(
        session_token="tok",
        api_key="SECRET_KEY",
        org_id="org_x",
        config=DataLoaderConfig(),
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[],
        skip_refs={"ledger.le1"},
    )
    d = loader_draft_from_session(s)
    dumped = d.model_dump_json()
    assert "SECRET_KEY" not in dumped
    assert "tok" not in dumped
    assert "ledger.le1" in d.skip_refs


def test_merge_loader_draft_refreshes_batches() -> None:
    s = SessionState(
        session_token="t",
        api_key="k",
        org_id="o",
        config=DataLoaderConfig(),
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[],
    )
    draft = LoaderDraft(
        org_id="o",
        config_json_text="{}",
        skip_refs=[],
        generation_recipes={"f1": {"version": "v1", "flow_ref": "f1", "instances": 1}},
    )
    merge_loader_draft_into_session(s, draft)
    assert "f1" in s.generation_recipes
