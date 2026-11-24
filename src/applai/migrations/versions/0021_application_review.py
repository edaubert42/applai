"""Review of a filled application: problems found, the agent run, and any review error.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("review_problems", sa.JSON(), nullable=True))
    # SQLite supports ADD COLUMN ... REFERENCES in place without copying the table (which is
    # referenced by draft_answers, labels, state_changes and agent_runs).
    op.execute(
        "ALTER TABLE applications ADD COLUMN review_agent_run_id INTEGER REFERENCES agent_runs(id)"
    )
    op.add_column("applications", sa.Column("review_error", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("review_error")
        batch.drop_column("review_agent_run_id")
        batch.drop_column("review_problems")
