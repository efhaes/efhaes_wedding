from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class Guest(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "guests"
    __table_args__ = (
        CheckConstraint(
            "checkin_status IN ('pending', 'checked_in', 'no_show')",
            name="ck_guests_checkin_status",
        ),
        CheckConstraint(
            "(checkin_status = 'checked_in' AND checked_in_at IS NOT NULL) OR "
            "(checkin_status <> 'checked_in' AND checked_in_at IS NULL)",
            name="ck_guests_checkin_timestamp",
        ),
        Index("ix_guests_invitation_id", "invitation_id"),
        Index("ix_guests_invitation_checkin", "invitation_id", "checkin_status"),
        UniqueConstraint(
            "invitation_id",
            "id",
            name="uq_guests_invitation_id_id",
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(80))
    invitation_token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    checkin_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
