"""The sentences of a drafted answer the fact-faithfulness judge found unsupported.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # An optional column, added in place: answers drafted before it stay unjudged (None).
    op.add_column("draft_answers", sa.Column("judge_issues", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("draft_answers", "judge_issues")
