from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, UUIDPrimaryKey


class InvitationVersion(UUIDPrimaryKey, Base):
    __tablename__ = "invitation_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="ck_invitation_versions_version_positive"),
        Index("ix_invitation_versions_invitation_version", "invitation_id", "version_number", unique=True),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
