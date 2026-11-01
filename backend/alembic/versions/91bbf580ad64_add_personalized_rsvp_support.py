"""add personalized RSVP support

Revision ID: 91bbf580ad64
Revises: 72a6e43b19df
Create Date: 2026-10-31 19:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91bbf580ad64"
down_revision: Union[str, Sequence[str], None] = "72a6e43b19df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_guests_invitation_id_id",
        "guests",
        ["invitation_id", "id"],
    )
    op.add_column("rsvps", sa.Column("guest_id", sa.Uuid(), nullable=True))
    op.add_column(
        "rsvps",
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_rsvps_guest_count_positive",
        "rsvps",
        type_="check",
    )
    op.create_check_constraint(
        "ck_rsvps_guest_count_nonnegative",
        "rsvps",
        "guest_count >= 0",
    )
    op.create_foreign_key(
        "fk_rsvps_invitation_guest",
        "rsvps",
        "guests",
        ["invitation_id", "guest_id"],
        ["invitation_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_rsvps_invitation_guest",
        "rsvps",
        ["invitation_id", "guest_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    rsvp_table = sa.table(
        "rsvps",
        sa.column("attendance_status", sa.String(length=20)),
        sa.column("guest_count", sa.Integer()),
    )
    connection.execute(
        rsvp_table.update()
        .where(
            rsvp_table.c.attendance_status == "declined",
            rsvp_table.c.guest_count == 0,
        )
        .values(guest_count=1)
    )
    op.drop_constraint(
        "uq_rsvps_invitation_guest",
        "rsvps",
        type_="unique",
    )
    op.drop_constraint(
        "fk_rsvps_invitation_guest",
        "rsvps",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_rsvps_guest_count_nonnegative",
        "rsvps",
        type_="check",
    )
    op.create_check_constraint(
        "ck_rsvps_guest_count_positive",
        "rsvps",
        "guest_count > 0",
    )
    op.drop_column("rsvps", "responded_at")
    op.drop_column("rsvps", "guest_id")
    op.drop_constraint(
        "uq_guests_invitation_id_id",
        "guests",
        type_="unique",
    )
