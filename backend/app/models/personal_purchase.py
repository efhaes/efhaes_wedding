from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class PersonalPurchase(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "personal_purchases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'expired', 'cancelled', 'refunded')",
            name="ck_personal_purchases_status",
        ),
        CheckConstraint(
            "expires_at IS NULL OR activated_at IS NULL OR expires_at > activated_at",
            name="ck_personal_purchases_expiry_after_activation",
        ),
        ForeignKeyConstraint(
            ["invitation_id", "user_id"],
            ["invitations.id", "invitations.owner_id"],
            name="fk_personal_purchases_invitation_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_personal_purchases_user_id", "user_id"),
        Index("ix_personal_purchases_invitation_id", "invitation_id"),
        Index("ix_personal_purchases_plan_id", "plan_id"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    invitation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
