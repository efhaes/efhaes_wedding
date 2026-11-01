"""add_auth_roles_and_refresh_revocation

Revision ID: 8a14e4cfb9c1
Revises: 3e1c938b7663
Create Date: 2026-10-31 17:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a14e4cfb9c1"
down_revision: Union[str, Sequence[str], None] = "3e1c938b7663"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_workspace_members_role", "workspace_members", type_="check")
    op.create_check_constraint(
        "ck_workspace_members_role",
        "workspace_members",
        "role IN ('owner', 'admin', 'editor', 'member')",
    )
    op.add_column(
        "users",
        sa.Column("platform_role", sa.String(length=20), server_default="user", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_platform_role", "users", "platform_role IN ('user', 'admin')"
    )
    op.add_column(
        "users", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "refresh_tokens", sa.Column("revoked_reason", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "revoked_reason")
    op.drop_column("users", "session_version")
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
    op.drop_column("users", "platform_role")
    op.drop_constraint("ck_workspace_members_role", "workspace_members", type_="check")
    op.create_check_constraint(
        "ck_workspace_members_role",
        "workspace_members",
        "role IN ('owner', 'admin', 'member')",
    )
