"""add expiration to entitlement overrides

Revision ID: e75b2f6d4a90
Revises: d0a6e74b291c
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e75b2f6d4a90"
down_revision: Union[str, Sequence[str], None] = "d0a6e74b291c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entitlement_overrides",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entitlement_overrides", "expires_at")
