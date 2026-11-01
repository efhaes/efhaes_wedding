from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
)
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.guest import Guest
from app.modules.invitations.models.invitation import Invitation


class RSVP(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "rsvps"
    __table_args__ = (
        CheckConstraint(
            "attendance_status IN ('pending', 'attending', 'declined')",
            name="ck_rsvps_attendance_status",
        ),
        CheckConstraint("guest_count >= 0", name="ck_rsvps_guest_count_nonnegative"),
        ForeignKeyConstraint(
            ["invitation_id", "guest_id"],
            ["guests.invitation_id", "guests.id"],
            name="fk_rsvps_invitation_guest",
            ondelete="CASCADE",
        ),
        Index("ix_rsvps_invitation_id", "invitation_id"),
        UniqueConstraint(
            "invitation_id",
            "guest_id",
            name="uq_rsvps_invitation_guest",
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    guest_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    guest_name: Mapped[str] = mapped_column(String(160), nullable=False)
    attendance_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    guest_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    message: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    invitation: Mapped[Invitation] = relationship(foreign_keys=[invitation_id])
    guest: Mapped[Guest | None] = relationship(
        primaryjoin=lambda: and_(
            foreign(RSVP.invitation_id) == Guest.invitation_id,
            foreign(RSVP.guest_id) == Guest.id,
        ),
        foreign_keys=lambda: [RSVP.invitation_id, RSVP.guest_id],
        viewonly=True,
    )
