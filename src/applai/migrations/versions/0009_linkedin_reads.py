"""The reads of LinkedIn offers through the candidate's MCP server are kept, to limit them.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "linkedin_reads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], name=op.f("fk_linkedin_reads_offer_id_offers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_linkedin_reads")),
    )
    op.create_index(op.f("ix_linkedin_reads_read_at"), "linkedin_reads", ["read_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_linkedin_reads_read_at"), table_name="linkedin_reads")
    op.drop_table("linkedin_reads")
