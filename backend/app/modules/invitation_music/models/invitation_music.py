from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Float,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class InvitationMusic(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invitation_music"
    __table_args__ = (
        CheckConstraint("file_size > 0", name="ck_invitation_music_file_size_positive"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="ck_invitation_music_duration_positive",
        ),
        CheckConstraint("mime_type = 'audio/mpeg'", name="ck_invitation_music_mime_type"),
        UniqueConstraint("invitation_id", name="uq_invitation_music_invitation_id"),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invitations.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(80), nullable=False, default="audio/mpeg", server_default="audio/mpeg"
    )
    file_size: Mapped[int] = mapped_column(nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    autoplay_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
