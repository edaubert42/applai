"""Labels of the answers gate: the answer they are about, and the text the candidate turned
down. Labels written before keep both empty.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite adds a foreign key only by copying the table: labels keep their rows and ids,
    # and no table references labels.
    with op.batch_alter_table("labels") as batch:
        batch.add_column(sa.Column("answer_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rejected_text", sa.String(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_labels_answer_id_draft_answers"), "draft_answers", ["answer_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("labels") as batch:
        batch.drop_constraint(op.f("fk_labels_answer_id_draft_answers"), type_="foreignkey")
        batch.drop_column("rejected_text")
        batch.drop_column("answer_id")
