"""allow user-scoped unassigned personal purchases

Revision ID: f2c8a9d0137e
Revises: e75b2f6d4a90
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2c8a9d0137e"
down_revision: Union[str, Sequence[str], None] = "e75b2f6d4a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "personal_purchases",
        "invitation_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_personal_purchases_user_id",
        "personal_purchases",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    bind = op.get_bind()
    unassigned_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM personal_purchases WHERE invitation_id IS NULL"
        )
    ).scalar_one()
    if unassigned_count:
        raise RuntimeError(
            "Cannot downgrade while unassigned personal purchases exist"
        )
    op.drop_constraint(
        "fk_personal_purchases_user_id",
        "personal_purchases",
        type_="foreignkey",
    )
    op.alter_column(
        "personal_purchases",
        "invitation_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
