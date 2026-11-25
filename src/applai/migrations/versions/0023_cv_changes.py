"""CV tailoring: the changes kept on an application's CV, its title line, photo and warning.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None

_COLUMNS = ("cv_headline", "cv_photo", "cv_photo_reason", "cv_warning")


def upgrade() -> None:
    for name in _COLUMNS:  # optional columns, added in place: applications keep their rows
        op.add_column("applications", sa.Column(name, sa.String(), nullable=True))
    op.create_table(
        "cv_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("op", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("old_text", sa.String(), nullable=False),
        sa.Column("new_text", sa.String(), nullable=True),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_cv_changes_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cv_changes")),
    )
    op.create_index(op.f("ix_cv_changes_application_id"), "cv_changes", ["application_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_cv_changes_application_id"), table_name="cv_changes")
    op.drop_table("cv_changes")
    for name in reversed(_COLUMNS):
        op.drop_column("applications", name)
