"""Fill again on an application: when the candidate asked to fill its form again in a new tab.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("fill_again_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "fill_again_at")
