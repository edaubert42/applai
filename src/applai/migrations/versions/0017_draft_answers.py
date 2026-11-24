"""The answers and cover letters the Drafting agent writes, and why the last Drafting failed.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # An optional column, added in place: the applications keep their rows and ids.
    op.add_column("applications", sa.Column("draft_error", sa.String(), nullable=True))
    op.create_table(
        "draft_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("field_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("sentences", sa.JSON(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_draft_answers_agent_run_id_agent_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_draft_answers_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft_answers")),
    )
    op.create_index(op.f("ix_draft_answers_application_id"), "draft_answers", ["application_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_draft_answers_application_id"), table_name="draft_answers")
    op.drop_table("draft_answers")
    op.drop_column("applications", "draft_error")
