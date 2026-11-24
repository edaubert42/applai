"""Offers keep a request to read their page again, and why the last one failed.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Optional columns, added in place: the offers already there keep their rows and ids.
    op.add_column("offers", sa.Column("reread_requested_at", sa.DateTime(), nullable=True))
    op.add_column("offers", sa.Column("reread_error", sa.String(), nullable=True))
    op.add_column("offers", sa.Column("reread_failed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "reread_failed_at")
    op.drop_column("offers", "reread_error")
    op.drop_column("offers", "reread_requested_at")
