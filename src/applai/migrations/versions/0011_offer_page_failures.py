"""Offers count the pages the ApplAI extension could not read, and keep when the last failed.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Added in place, with a default: the offers already there keep their rows and ids.
    op.add_column(
        "offers", sa.Column("page_failures", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("offers", sa.Column("page_failed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying offers, which other tables reference.
    op.drop_column("offers", "page_failed_at")
    op.drop_column("offers", "page_failures")
