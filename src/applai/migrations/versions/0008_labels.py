"""The candidate's decisions are labels.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("correction", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], name=op.f("fk_labels_offer_id_offers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labels")),
    )
    op.create_index(op.f("ix_labels_offer_id"), "labels", ["offer_id"])


def downgrade() -> None:
    op.drop_table("labels")
