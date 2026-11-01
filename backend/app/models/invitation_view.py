from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKey


class InvitationView(UUIDPrimaryKey, Base):
    __tablename__ = "invitation_views"
    __table_args__ = (
        Index("ix_invitation_views_invitation_viewed_at", "invitation_id", "viewed_at"),
        UniqueConstraint(
            "invitation_id",
            "visitor_key_hash",
            name="uq_invitation_views_invitation_visitor",
        ),
        ForeignKeyConstraint(
            ["invitation_id", "guest_id"],
            ["guests.invitation_id", "guests.id"],
            name="fk_invitation_views_invitation_guest",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    guest_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("guests.id", ondelete="SET NULL")
    )
    visitor_key_hash: Mapped[str | None] = mapped_column(String(64))
