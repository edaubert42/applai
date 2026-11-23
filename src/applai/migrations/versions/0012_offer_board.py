"""Offers keep the job board their alert says they come via.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # An optional column, added in place: the offers already there keep their rows and ids.
    op.add_column("offers", sa.Column("board", sa.String(), nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying offers, which other tables reference.
    op.drop_column("offers", "board")
