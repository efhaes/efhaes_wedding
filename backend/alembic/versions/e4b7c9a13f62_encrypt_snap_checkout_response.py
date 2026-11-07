"""store encrypted Snap checkout responses

Revision ID: e4b7c9a13f62
Revises: d83f6a1c9b42
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7c9a13f62"
down_revision: Union[str, Sequence[str], None] = "d83f6a1c9b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checkout_attempts",
        sa.Column("provider_response_ciphertext", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    stored_responses = bind.execute(
        sa.text(
            "SELECT count(*) FROM checkout_attempts "
            "WHERE provider_response_ciphertext IS NOT NULL"
        )
    ).scalar_one()
    if stored_responses:
        raise RuntimeError(
            "Cannot downgrade while encrypted Snap checkout responses exist."
        )
    op.drop_column("checkout_attempts", "provider_response_ciphertext")
