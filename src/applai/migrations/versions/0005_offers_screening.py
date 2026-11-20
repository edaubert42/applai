"""Offers keep what the first screening needs, and the rule that set one aside.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

_COLUMNS = (
    ("work_mode", sa.String()),
    ("years_required", sa.Integer()),
    ("screened_rule", sa.String()),
    ("screen_note", sa.String()),
)


def upgrade() -> None:
    # Optional columns, added in place: the offers already there keep their rows and ids.
    for name, type_ in _COLUMNS:
        op.add_column("offers", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying offers, which other tables reference.
    for name, _ in reversed(_COLUMNS):
        op.drop_column("offers", name)
