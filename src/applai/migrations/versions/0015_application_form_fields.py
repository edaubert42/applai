"""Applications keep their form's fields, and count the forms the extension could not read.

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
    # Added in place, optional or with a default: the applications keep their rows and ids.
    op.add_column("applications", sa.Column("form_fields", sa.JSON(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("form_failures", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("applications", sa.Column("form_failed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # In place (SQLite 3.35+), not by copying applications, which other tables reference.
    op.drop_column("applications", "form_failed_at")
    op.drop_column("applications", "form_failures")
    op.drop_column("applications", "form_fields")
