"""Form failed page on an application: the page of a form read page by page its form_url_error
is about, cleared when that page is posted.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision: str | None = "0025"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("form_failed_page", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "form_failed_page")
