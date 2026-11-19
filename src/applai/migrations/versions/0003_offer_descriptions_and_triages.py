"""An offer's text, and the card the Triage agent writes for it.

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
    # Nullable, so the offers already stored keep their rows as they are.
    op.add_column("offers", sa.Column("description", sa.String(), nullable=True))
    op.create_table(
        "triages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("about", sa.String(), nullable=False),
        sa.Column("mission", sa.JSON(), nullable=False),
        sa.Column("pros", sa.JSON(), nullable=False),
        sa.Column("cons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], name=op.f("fk_triages_agent_run_id_agent_runs")
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], name=op.f("fk_triages_offer_id_offers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triages")),
        sa.UniqueConstraint("offer_id", name=op.f("uq_triages_offer_id")),
    )


def downgrade() -> None:
    op.drop_table("triages")
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("description")
