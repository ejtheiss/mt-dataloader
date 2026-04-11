"""DB-only run artifacts: normalized tables, drop correlation + manifest_json.

Revision ID: 20260420120000
Revises: 20260410180000
Create Date: 2026-04-20

Backfill reads ``runs/<run_id>.json`` and optional ``runs.manifest_json`` before
drop. ``DATALOADER_RUNS_DIR`` (default ``runs``) locates disk files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "20260420120000"
down_revision = "20260410180000"
branch_labels = None
depends_on = None


def _runs_dir() -> Path:
    return Path(os.environ.get("DATALOADER_RUNS_DIR", "runs"))


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _backfill(connection: sa.Connection) -> None:
    runs_dir = _runs_dir()
    run_rows = connection.execute(sa.text("SELECT run_id, manifest_json FROM runs")).fetchall()
    for run_id, manifest_json_col in run_rows:
        manifest: dict[str, Any] | None = None
        disk_m = runs_dir / f"{run_id}.json"
        if disk_m.is_file():
            manifest = _load_json_file(disk_m)
        if manifest is None and manifest_json_col:
            try:
                manifest = json.loads(manifest_json_col)
            except (json.JSONDecodeError, TypeError):
                manifest = None

        cfg_path = runs_dir / f"{run_id}_config.json"
        cfg_text: str | None = None
        if cfg_path.is_file():
            try:
                cfg_text = cfg_path.read_text(encoding="utf-8")
            except OSError:
                cfg_text = None

        extras: dict[str, Any] = {}
        if manifest:
            for k in ("generation_recipe", "compile_id", "seed_version"):
                v = manifest.get(k)
                if v is not None:
                    extras[k] = v
        extras_json = json.dumps(extras) if extras else None

        connection.execute(
            sa.text(
                "UPDATE runs SET config_json = COALESCE(:cfg, config_json), "
                "run_extras_json = COALESCE(:ex, run_extras_json) WHERE run_id = :rid"
            ),
            {"cfg": cfg_text, "ex": extras_json, "rid": run_id},
        )

        if not manifest:
            continue

        for e in manifest.get("resources_created") or []:
            cid = (e.get("created_id") or "").strip()
            if not cid or cid == "SKIPPED":
                continue
            child_refs = e.get("child_refs") or {}
            connection.execute(
                sa.text(
                    """
                    INSERT OR REPLACE INTO run_created_resources (
                        created_id, run_id, batch, resource_type, typed_ref,
                        created_at, deletable, cleanup_status, child_refs_json
                    ) VALUES (
                        :created_id, :run_id, :batch, :resource_type, :typed_ref,
                        :created_at, :deletable, :cleanup_status, :child_refs_json
                    )
                    """
                ),
                {
                    "created_id": cid,
                    "run_id": run_id,
                    "batch": int(e.get("batch", 0)),
                    "resource_type": str(e.get("resource_type", "")),
                    "typed_ref": str(e.get("typed_ref", "")),
                    "created_at": str(e.get("created_at", "")),
                    "deletable": 1 if e.get("deletable") else 0,
                    "cleanup_status": e.get("cleanup_status"),
                    "child_refs_json": json.dumps(child_refs),
                },
            )

        for f in manifest.get("resources_failed") or []:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO run_resource_failures (
                        run_id, typed_ref, error, failed_at,
                        error_type, http_status, error_cause
                    ) VALUES (
                        :run_id, :typed_ref, :error, :failed_at,
                        :error_type, :http_status, :error_cause
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "typed_ref": str(f.get("typed_ref", "")),
                    "error": str(f.get("error", "")),
                    "failed_at": str(f.get("failed_at", "")),
                    "error_type": f.get("error_type"),
                    "http_status": f.get("http_status"),
                    "error_cause": f.get("error_cause"),
                },
            )

        staged_path = runs_dir / f"{run_id}_staged.json"
        staged_payloads: dict[str, Any] = {}
        if staged_path.is_file():
            raw = _load_json_file(staged_path)
            if isinstance(raw, dict):
                staged_payloads = raw

        for s in manifest.get("resources_staged") or []:
            tref = str(s.get("typed_ref", ""))
            if not tref:
                continue
            payload = staged_payloads.get(tref, {})
            connection.execute(
                sa.text(
                    """
                    INSERT OR REPLACE INTO run_staged_items (
                        run_id, typed_ref, resource_type, staged_at, payload_json
                    ) VALUES (
                        :run_id, :typed_ref, :resource_type, :staged_at, :payload_json
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "typed_ref": tref,
                    "resource_type": str(s.get("resource_type", "")),
                    "staged_at": str(s.get("staged_at", "")),
                    "payload_json": json.dumps(payload),
                },
            )

    # Orphan correlation rows → minimal created rows (webhook safety).
    try:
        orphans = connection.execute(
            sa.text("SELECT created_id, run_id, typed_ref FROM resource_correlation")
        ).fetchall()
    except Exception:
        orphans = []

    for created_id, rid, typed_ref in orphans:
        exists = connection.execute(
            sa.text("SELECT 1 FROM run_created_resources WHERE created_id = :c LIMIT 1"),
            {"c": created_id},
        ).fetchone()
        if exists:
            continue
        connection.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO run_created_resources (
                    created_id, run_id, batch, resource_type, typed_ref,
                    created_at, deletable, cleanup_status, child_refs_json
                ) VALUES (
                    :created_id, :run_id, -1, 'unknown', :typed_ref,
                    '1970-01-01T00:00:00+00:00', 0, NULL, '{}'
                )
                """
            ),
            {"created_id": created_id, "run_id": rid, "typed_ref": typed_ref},
        )


def upgrade() -> None:
    op.create_table(
        "run_created_resources",
        sa.Column("created_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("batch", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("typed_ref", sa.String(512), nullable=False),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("deletable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cleanup_status", sa.String(64), nullable=True),
        sa.Column("child_refs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("created_id"),
    )
    op.create_index("ix_run_created_run_id", "run_created_resources", ["run_id"])
    op.create_index(
        "uq_run_created_run_typed_ref",
        "run_created_resources",
        ["run_id", "typed_ref"],
        unique=True,
    )

    op.create_table(
        "run_resource_failures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("typed_ref", sa.String(512), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("failed_at", sa.String(64), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_cause", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_failures_run_id", "run_resource_failures", ["run_id"])

    op.create_table(
        "run_staged_items",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("typed_ref", sa.String(512), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("staged_at", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "typed_ref"),
    )

    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("config_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("run_extras_json", sa.Text(), nullable=True))

    connection = op.get_bind()
    _backfill(connection)

    op.drop_table("resource_correlation")

    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_column("manifest_json")


def downgrade() -> None:
    op.drop_table("run_staged_items")
    op.drop_table("run_resource_failures")
    op.drop_table("run_created_resources")

    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("manifest_json", sa.Text(), nullable=True))
        batch_op.drop_column("config_json")
        batch_op.drop_column("run_extras_json")

    op.create_table(
        "resource_correlation",
        sa.Column("created_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("typed_ref", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("created_id"),
    )
