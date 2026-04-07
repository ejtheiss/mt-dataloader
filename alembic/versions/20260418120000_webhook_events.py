"""webhook_events table — Wave C durable webhook log (SQLite)

Revision ID: 20260418120000
Revises: 20260417120000
Create Date: 2026-04-18

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260418120000"
down_revision = "20260417120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("webhook_id", sa.String(length=256), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("typed_ref", sa.String(length=512), nullable=True),
        sa.Column("received_at", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=256), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("webhook_id", name="uq_webhook_events_webhook_id"),
    )
    op.create_index(
        "ix_webhook_events_run_id_received_at",
        "webhook_events",
        ["run_id", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_run_id_received_at", table_name="webhook_events")
    op.drop_table("webhook_events")
