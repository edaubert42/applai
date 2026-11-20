"""The candidate's decisions are labels, and offers keep their company's logo.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Optional, added in place: the offers already there keep their rows and ids.
    op.add_column("offers", sa.Column("logo_url", sa.String(), nullable=True))
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
    op.drop_column("offers", "logo_url")
