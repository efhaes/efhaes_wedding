"""add_template_catalog_fields

Revision ID: 4d87bc9ef201
Revises: 8a14e4cfb9c1
Create Date: 2026-10-31 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d87bc9ef201"
down_revision: Union[str, Sequence[str], None] = "8a14e4cfb9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("templates", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "templates", sa.Column("preview_image_url", sa.String(length=2048), nullable=True)
    )
    op.add_column(
        "templates",
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=True),
    )
    op.execute(
        "UPDATE templates SET status = CASE "
        "WHEN is_active THEN 'published' ELSE 'archived' END"
    )
    op.alter_column(
        "templates",
        "status",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="draft",
    )
    op.create_check_constraint(
        "ck_templates_status",
        "templates",
        "status IN ('draft', 'published', 'archived')",
    )
    op.create_index(
        "ix_templates_status_category", "templates", ["status", "category"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_templates_status_category", table_name="templates")
    op.drop_constraint("ck_templates_status", "templates", type_="check")
    op.drop_column("templates", "status")
    op.drop_column("templates", "preview_image_url")
    op.drop_column("templates", "description")
