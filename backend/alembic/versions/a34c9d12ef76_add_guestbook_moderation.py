"""add guestbook moderation and personalized guests

Revision ID: a34c9d12ef76
Revises: 91bbf580ad64
Create Date: 2026-10-31 19:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a34c9d12ef76"
down_revision: Union[str, Sequence[str], None] = "91bbf580ad64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guestbook_entries",
        sa.Column("guest_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "guestbook_entries",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=True,
        ),
    )
    op.add_column(
        "guestbook_entries",
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guestbook_entries",
        sa.Column("moderated_by", sa.Uuid(), nullable=True),
    )
    guestbook_table = sa.table(
        "guestbook_entries",
        sa.column("is_approved", sa.Boolean()),
        sa.column("status", sa.String(length=20)),
    )
    op.get_bind().execute(
        guestbook_table.update()
        .where(guestbook_table.c.is_approved.is_(True))
        .values(status="approved")
    )
    op.alter_column(
        "guestbook_entries",
        "status",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="pending",
    )
    op.create_check_constraint(
        "ck_guestbook_entries_status",
        "guestbook_entries",
        "status IN ('pending', 'approved', 'hidden')",
    )
    op.create_foreign_key(
        "fk_guestbook_entries_invitation_guest",
        "guestbook_entries",
        "guests",
        ["invitation_id", "guest_id"],
        ["invitation_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_guestbook_entries_moderated_by",
        "guestbook_entries",
        "users",
        ["moderated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_guestbook_entries_invitation_status_created",
        "guestbook_entries",
        ["invitation_id", "status", "created_at"],
        unique=False,
    )
    op.drop_index(
        "ix_guestbook_entries_invitation_approved",
        table_name="guestbook_entries",
    )


def downgrade() -> None:
    guestbook_table = sa.table(
        "guestbook_entries",
        sa.column("is_approved", sa.Boolean()),
        sa.column("status", sa.String(length=20)),
    )
    op.get_bind().execute(
        guestbook_table.update()
        .where(guestbook_table.c.status == "approved")
        .values(is_approved=True)
    )
    op.drop_index(
        "ix_guestbook_entries_invitation_status_created",
        table_name="guestbook_entries",
    )
    op.create_index(
        "ix_guestbook_entries_invitation_approved",
        "guestbook_entries",
        ["invitation_id", "is_approved"],
        unique=False,
    )
    op.drop_constraint(
        "fk_guestbook_entries_moderated_by",
        "guestbook_entries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_guestbook_entries_invitation_guest",
        "guestbook_entries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_guestbook_entries_status",
        "guestbook_entries",
        type_="check",
    )
    op.drop_column("guestbook_entries", "moderated_by")
    op.drop_column("guestbook_entries", "moderated_at")
    op.drop_column("guestbook_entries", "status")
    op.drop_column("guestbook_entries", "guest_id")
