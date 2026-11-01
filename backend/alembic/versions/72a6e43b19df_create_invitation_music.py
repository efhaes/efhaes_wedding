"""create invitation music

Revision ID: 72a6e43b19df
Revises: f4d2a6b9810c
Create Date: 2026-10-31 19:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "72a6e43b19df"
down_revision: Union[str, Sequence[str], None] = "f4d2a6b9810c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invitation_music",
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column(
            "mime_type",
            sa.String(length=80),
            server_default="audio/mpeg",
            nullable=False,
        ),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "autoplay_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "file_size > 0",
            name="ck_invitation_music_file_size_positive",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="ck_invitation_music_duration_positive",
        ),
        sa.CheckConstraint(
            "mime_type = 'audio/mpeg'",
            name="ck_invitation_music_mime_type",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id", name="uq_invitation_music_invitation_id"),
        sa.UniqueConstraint("storage_key", name="uq_invitation_music_storage_key"),
    )

def downgrade() -> None:
    op.drop_table("invitation_music")
