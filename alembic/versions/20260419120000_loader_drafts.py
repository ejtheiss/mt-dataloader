"""loader_drafts — Wave D durable loader continuity (one row per user)

Revision ID: 20260419120000
Revises: 20260418120000
Create Date: 2026-04-19

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260419120000"
down_revision = "20260418120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loader_drafts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("draft_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("last_run_id", sa.String(length=128), nullable=True),
        sa.Column("last_run_at", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("loader_drafts")
