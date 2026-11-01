"""add invitation section identity

Revision ID: c318a0db4e71
Revises: 4d87bc9ef201
Create Date: 2026-10-31 18:26:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c318a0db4e71"
down_revision: Union[str, Sequence[str], None] = "4d87bc9ef201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invitation_sections",
        sa.Column("section_key", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "invitation_sections",
        sa.Column("title", sa.String(length=120), nullable=True),
    )

    section_rows = sa.table(
        "invitation_sections",
        sa.column("id", sa.Uuid()),
        sa.column("section_type", sa.String(length=80)),
        sa.column("section_key", sa.String(length=80)),
        sa.column("title", sa.String(length=120)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(section_rows.c.id, section_rows.c.section_type)
    ).all()
    for row in rows:
        section_type = row.section_type or "section"
        connection.execute(
            section_rows.update()
            .where(section_rows.c.id == row.id)
            .values(
                section_key=f"legacy_{str(row.id).replace('-', '')}",
                title=section_type.replace("_", " ").strip().title()[:120] or "Section",
            )
        )

    op.alter_column(
        "invitation_sections",
        "section_key",
        existing_type=sa.String(length=80),
        nullable=False,
    )
    op.alter_column(
        "invitation_sections",
        "title",
        existing_type=sa.String(length=120),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_invitation_sections_invitation_key",
        "invitation_sections",
        ["invitation_id", "section_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_invitation_sections_invitation_key",
        "invitation_sections",
        type_="unique",
    )
    op.drop_column("invitation_sections", "title")
    op.drop_column("invitation_sections", "section_key")
