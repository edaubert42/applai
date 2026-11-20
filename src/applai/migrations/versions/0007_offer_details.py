"""Offers keep what their page gives beyond the description, and why it could not be read.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None

_COLUMNS = ("salary", "logo", "detail_error")


def upgrade() -> None:
    # Optional columns, added in place: the offers already there keep their rows and ids.
    for name in _COLUMNS:
        op.add_column("offers", sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying offers, which other tables reference.
    for name in reversed(_COLUMNS):
        op.drop_column("offers", name)
