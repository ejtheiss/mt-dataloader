"""runs list indexes — user+started_at and global started_at (Plan 09)

Revision ID: 20260410180000
Revises: 20260419120000
Create Date: 2026-04-10

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260410180000"
down_revision = "20260419120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 3.30+ supports DESC in index columns; speeds up scoped + default-order lists.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_runs_list_user_started_run "
            "ON runs (user_id, started_at DESC, run_id DESC)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_runs_list_started_run "
            "ON runs (started_at DESC, run_id DESC)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_runs_list_user_started_run"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_runs_list_started_run"))
