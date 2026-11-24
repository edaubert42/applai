"""Labels of the answers gate: their application and the word diff of an edit; edited answers.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("draft_answers", sa.Column("edited_text", sa.String(), nullable=True))
    # SQLite adds a foreign key only by copying the table: labels keep their rows and ids,
    # and no table references labels.
    with op.batch_alter_table("labels") as batch:
        batch.add_column(sa.Column("application_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("diff", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_labels_application_id_applications"),
            "applications",
            ["application_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("labels") as batch:
        batch.drop_constraint(op.f("fk_labels_application_id_applications"), type_="foreignkey")
        batch.drop_column("diff")
        batch.drop_column("application_id")
    op.drop_column("draft_answers", "edited_text")
