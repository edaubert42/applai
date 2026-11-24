"""Offers keep when their page last failed to be read.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # An optional column, added in place: the offers already there keep their rows and ids.
    op.add_column("offers", sa.Column("detail_failed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "detail_failed_at")
