"""Applications keep the URL of their offer's form, or why it was not found.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Optional columns, added in place: the applications already there keep their rows and ids.
    op.add_column("applications", sa.Column("form_url", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("form_url_error", sa.String(), nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying applications, which other tables reference.
    op.drop_column("applications", "form_url_error")
    op.drop_column("applications", "form_url")
