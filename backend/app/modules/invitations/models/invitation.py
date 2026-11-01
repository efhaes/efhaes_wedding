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
    from app.models.workspace import Workspace
    from app.modules.templates.models.template import Template
    from app.modules.users.models.user import User


class Invitation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL AND workspace_id IS NULL AND client_id IS NULL) OR "
            "(owner_id IS NULL AND workspace_id IS NOT NULL)",
            name="ck_invitations_exactly_one_owner",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_invitations_status",
        ),
        CheckConstraint(
            "expires_at IS NULL OR first_published_at IS NULL OR expires_at > first_published_at",
            name="ck_invitations_expiry_after_publish",
        ),
        UniqueConstraint("id", "owner_id", name="uq_invitations_id_owner"),
        UniqueConstraint("workspace_id", "id", name="uq_invitations_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_invitations_workspace_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_invitations_client_workspace",
            ondelete="RESTRICT",
        ),
        Index("ix_invitations_owner_id", "owner_id"),
        Index("ix_invitations_workspace_id", "workspace_id"),
        Index("ix_invitations_client_id", "client_id"),
        Index("ix_invitations_template_id", "template_id"),
        Index("ix_invitations_status", "status"),
    )

    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    workspace_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    client_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    template_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("templates.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_id])
    workspace: Mapped["Workspace | None"] = relationship(foreign_keys=[workspace_id])
    template: Mapped["Template"] = relationship()
