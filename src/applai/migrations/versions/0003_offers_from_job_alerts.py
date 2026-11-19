"""Offers keep what a job alert gives about them; the alerts read are kept.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Optional columns, added in place: the offers already there keep their rows and ids.
    op.add_column("offers", sa.Column("location", sa.String(), nullable=True))
    op.add_column("offers", sa.Column("contract", sa.String(), nullable=True))
    op.add_column("offers", sa.Column("source", sa.String(), nullable=True))
    op.add_column("offers", sa.Column("received_at", sa.DateTime(), nullable=True))
    op.create_table(
        "mails_read",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mails_read")),
        sa.UniqueConstraint("user_id", "message_id", name=op.f("uq_mails_read_user_id_message_id")),
    )


def downgrade() -> None:
    op.drop_table("mails_read")
    # In place (SQLite 3.35+), not by copying offers, which other tables reference.
    for column in ("received_at", "source", "contract", "location"):
        op.drop_column("offers", column)
