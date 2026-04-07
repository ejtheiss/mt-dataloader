"""runs.manifest_json canonical RunManifest snapshot (Wave B)

Revision ID: 20260416120000
Revises: 20260415120000
Create Date: 2026-04-16

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260416120000"
down_revision = "20260415120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("manifest_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "manifest_json")
