"""users.role — admin vs user (Plan 0 visibility)

Revision ID: 20260417120000
Revises: 20260416120000
Create Date: 2026-04-17

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260417120000"
down_revision = "20260416120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
    )
    op.execute(sa.text("UPDATE users SET role = 'admin' WHERE id = 1"))


def downgrade() -> None:
    op.drop_column("users", "role")
