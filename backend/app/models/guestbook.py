from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class GuestbookEntry(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "guestbook_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'hidden')",
            name="ck_guestbook_entries_status",
        ),
        ForeignKeyConstraint(
            ["invitation_id", "guest_id"],
            ["guests.invitation_id", "guests.id"],
            name="fk_guestbook_entries_invitation_guest",
            ondelete="CASCADE",
        ),
        Index(
            "ix_guestbook_entries_invitation_status_created",
            "invitation_id",
            "status",
            "created_at",
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    guest_name: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    guest_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
