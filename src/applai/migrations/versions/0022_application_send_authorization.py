"""Send authorization on an application: when the candidate authorized it, and why send failed.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("send_authorized_at", sa.DateTime(), nullable=True))
    op.add_column("applications", sa.Column("send_error", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "send_error")
    op.drop_column("applications", "send_authorized_at")
