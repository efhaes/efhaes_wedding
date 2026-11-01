"""add persistent rate limit buckets

Revision ID: d0a6e74b291c
Revises: b8912c64f3a1
Create Date: 2026-11-01 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0a6e74b291c"
down_revision: Union[str, Sequence[str], None] = "b8912c64f3a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(
        "ix_rate_limit_buckets_reset_at",
        "rate_limit_buckets",
        ["reset_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_buckets_reset_at", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
