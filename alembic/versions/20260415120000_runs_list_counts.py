"""runs list resource counts for Wave B SQL listing

Revision ID: 20260415120000
Revises: 20260401120000
Create Date: 2026-04-15

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260415120000"
down_revision = "20260401120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("resources_created_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "runs",
        sa.Column("resources_staged_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "runs",
        sa.Column("resources_failed_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("runs", "resources_failed_count")
    op.drop_column("runs", "resources_staged_count")
    op.drop_column("runs", "resources_created_count")
