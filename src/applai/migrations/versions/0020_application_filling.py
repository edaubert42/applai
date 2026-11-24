"""Filling of an application: its fill plan, the filled state the extension reports, its error.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None

_COLUMNS = (
    ("fill_plan", sa.JSON()),
    ("filled_fields", sa.JSON()),
    ("fill_error", sa.String()),
    ("fill_failed_at", sa.DateTime()),
)


def upgrade() -> None:
    for name, kind in _COLUMNS:
        op.add_column("applications", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("applications", name)
