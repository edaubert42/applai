"""The candidate's edited text of a CV change, kept beside the drafted one.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cv_changes", sa.Column("edited_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("cv_changes", "edited_text")
