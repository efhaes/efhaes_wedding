from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, TimestampMixin, UUIDPrimaryKey
from app.models.plan import Plan


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
        CheckConstraint(
            "(duration_type_snapshot IS NULL AND duration_days_snapshot IS NULL) OR "
            "(duration_type_snapshot = 'fixed' AND "
            " duration_days_snapshot IS NOT NULL AND duration_days_snapshot > 0) OR "
            "(duration_type_snapshot = 'lifetime' AND duration_days_snapshot IS NULL)",
            name="ck_personal_purchases_duration_snapshot",
        ),
        CheckConstraint(
            "(purchased_price IS NULL AND purchased_currency IS NULL) OR "
            "(purchased_price IS NOT NULL AND purchased_price >= 0 AND "
            " ((purchased_price = 0 AND "
            "   (purchased_currency IS NULL OR length(purchased_currency) = 3)) OR "
            "  (purchased_price > 0 AND purchased_currency IS NOT NULL "
            "   AND length(purchased_currency) = 3)))",
            name="ck_personal_purchases_price_snapshot",
        ),
        ForeignKeyConstraint(
            ["invitation_id", "user_id"],
            ["invitations.id", "invitations.owner_id"],
            name="fk_personal_purchases_invitation_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_personal_purchases_user_id",
            ondelete="RESTRICT",
        ),
        Index("ix_personal_purchases_user_id", "user_id"),
        Index("ix_personal_purchases_invitation_id", "invitation_id"),
        Index("ix_personal_purchases_plan_id", "plan_id"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    invitation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_type_snapshot: Mapped[str | None] = mapped_column(String(20))
    duration_days_snapshot: Mapped[int | None] = mapped_column()
    purchased_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    purchased_currency: Mapped[str | None] = mapped_column(String(3))
    entitlement_snapshot: Mapped[JSONValue | None] = mapped_column(JSONB)

    @classmethod
    def pending_from_plan(
        cls,
        user_id: UUID,
        plan: Plan,
        entitlement_snapshot: JSONValue | None = None,
    ) -> "PersonalPurchase":
        if not plan.is_active or plan.billing_type != "one_time":
            raise ValueError("Personal purchases require an active one-time plan")
        if plan.duration_type == "fixed":
            if plan.duration_days is None or plan.duration_days <= 0:
                raise ValueError("Fixed-duration plan must define positive days")
        elif plan.duration_type == "lifetime":
            if plan.duration_days is not None:
                raise ValueError("Lifetime plan must not define duration days")
        else:
            raise ValueError("Personal purchase plan duration is not configured")
        return cls(
            user_id=user_id,
            plan_id=plan.id,
            status="pending",
            duration_type_snapshot=plan.duration_type,
            duration_days_snapshot=plan.duration_days,
            purchased_price=plan.price,
            purchased_currency=plan.currency,
            entitlement_snapshot=entitlement_snapshot,
        )

    def expiration_at(self, activated_at: datetime) -> datetime | None:
        if self.duration_type_snapshot == "lifetime":
            if self.duration_days_snapshot is not None:
                raise ValueError("Lifetime purchase snapshot must not include days")
            return None
        if (
            self.duration_type_snapshot == "fixed"
            and self.duration_days_snapshot is not None
            and self.duration_days_snapshot > 0
        ):
            return activated_at + timedelta(days=self.duration_days_snapshot)
        raise ValueError("Personal purchase duration snapshot is invalid")
