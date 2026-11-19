"""Where an offer comes from, and its status in the old workflow's Notion database.

Offers already in the database were all found by ApplAI: they get origin applai.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch_op:
        batch_op.add_column(
            sa.Column("origin", sa.String(), server_default="applai", nullable=False)
        )
        batch_op.add_column(sa.Column("notion_status", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("offers") as batch_op:
        batch_op.drop_column("notion_status")
        batch_op.drop_column("origin")
