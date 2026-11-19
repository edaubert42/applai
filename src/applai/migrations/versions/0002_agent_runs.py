"""The trace: one row per agent call.

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
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Double[float](), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_agent_runs_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], name=op.f("fk_agent_runs_offer_id_offers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_offer_id"), "agent_runs", ["offer_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_offer_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
