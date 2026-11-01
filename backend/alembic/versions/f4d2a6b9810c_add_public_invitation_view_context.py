"""add public invitation view context and hash legacy guest tokens

Revision ID: f4d2a6b9810c
Revises: c318a0db4e71
Create Date: 2026-10-31 18:48:00.000000

"""
from typing import Sequence, Union
import hashlib
import re

from alembic import op
import sqlalchemy as sa


revision: str = "f4d2a6b9810c"
down_revision: Union[str, Sequence[str], None] = "c318a0db4e71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    guest_table = sa.table(
        "guests",
        sa.column("id", sa.Uuid()),
        sa.column("invitation_token", sa.String(length=255)),
    )
    connection = op.get_bind()
    existing_guests = connection.execute(
        sa.select(guest_table.c.id, guest_table.c.invitation_token)
    ).all()
    for guest in existing_guests:
        token = guest.invitation_token
        if token and not re.fullmatch(r"[a-f0-9]{64}", token):
            connection.execute(
                guest_table.update()
                .where(guest_table.c.id == guest.id)
                .values(
                    invitation_token=hashlib.sha256(token.encode("utf-8")).hexdigest()
                )
            )

    op.add_column(
        "invitation_views",
        sa.Column("guest_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_invitation_views_guest_id_guests",
        "invitation_views",
        "guests",
        ["guest_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "invitation_views",
        sa.Column("visitor_key_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_invitation_views_invitation_visitor",
        "invitation_views",
        ["invitation_id", "visitor_key_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_invitation_views_invitation_visitor",
        "invitation_views",
        type_="unique",
    )
    op.drop_column("invitation_views", "visitor_key_hash")
    op.drop_constraint(
        "fk_invitation_views_guest_id_guests",
        "invitation_views",
        type_="foreignkey",
    )
    op.drop_column("invitation_views", "guest_id")
