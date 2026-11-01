from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, TimestampMixin, UUIDPrimaryKey


class InvitationSection(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invitation_sections"
    __table_args__ = (
        Index("ix_invitation_sections_invitation_order", "invitation_id", "sort_order"),
        UniqueConstraint(
            "invitation_id",
            "section_key",
            name="uq_invitation_sections_invitation_key",
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE"), nullable=False
    )
    section_key: Mapped[str] = mapped_column(String(80), nullable=False)
    section_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[JSONValue] = mapped_column(JSONB, nullable=False, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
