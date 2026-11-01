from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.client import Client
    from app.modules.users.models.user import User


class Workspace(UUIDPrimaryKey, TimestampMixin, Base):
    """Create the workspace and its active owner membership in one transaction."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name="ck_workspaces_status"
        ),
        ForeignKeyConstraint(
            ["id", "owner_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_workspaces_owner_membership",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")

    owner: Mapped["User"] = relationship(foreign_keys=[owner_id])
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace",
        foreign_keys="WorkspaceMember.workspace_id",
    )
    clients: Mapped[list["Client"]] = relationship(back_populates="workspace")


class WorkspaceMember(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'member')",
            name="ck_workspace_members_role",
        ),
        CheckConstraint("status IN ('active', 'invited', 'suspended')", name="ck_workspace_members_status"),
        Index("ix_workspace_members_user_id", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited", server_default="invited")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(
        back_populates="members",
        foreign_keys=[workspace_id],
    )
    user: Mapped["User"] = relationship(back_populates="memberships", foreign_keys=[user_id])