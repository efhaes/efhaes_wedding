from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKey


class InvitationMedia(UUIDPrimaryKey, Base):
    __tablename__ = "invitation_media"
    __table_args__ = (
        CheckConstraint("media_type IN ('image', 'music')", name="ck_invitation_media_type"),
        CheckConstraint("file_size >= 0", name="ck_invitation_media_file_size"),
        Index("ix_invitation_media_invitation_id", "invitation_id"),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
