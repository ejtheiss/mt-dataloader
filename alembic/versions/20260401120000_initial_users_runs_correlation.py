"""initial users runs correlation

Revision ID: 20260401120000
Revises:
Create Date: 2026-04-01

"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "20260401120000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(timezone.utc).isoformat()
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("mt_org_id", sa.String(length=128), nullable=True),
        sa.Column("mt_org_label", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("config_hash", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.String(length=64), nullable=False),
        sa.Column("completed_at", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_runs_user_id", "runs", ["user_id"], unique=False)
    op.create_index("ix_runs_mt_org_id", "runs", ["mt_org_id"], unique=False)

    op.create_table(
        "resource_correlation",
        sa.Column("created_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("typed_ref", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("created_id"),
    )

    op.execute(
        sa.text(
            "INSERT INTO users (id, created_at, email, display_name) "
            "VALUES (1, :created_at, NULL, :display_name)"
        ).bindparams(created_at=now, display_name="Default operator")
    )


def downgrade() -> None:
    op.drop_table("resource_correlation")
    op.drop_table("runs")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
