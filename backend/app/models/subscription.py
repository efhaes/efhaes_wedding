from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, TimestampMixin, UUIDPrimaryKey


class Subscription(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'trialing', 'active', 'past_due', 'cancelled', 'expired', 'refunded')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "(status IN ('pending', 'cancelled', 'expired', 'refunded') "
            "AND current_period_start IS NULL AND current_period_end IS NULL) OR "
            "(current_period_start IS NOT NULL AND current_period_end IS NOT NULL "
            "AND current_period_end > current_period_start)",
            name="ck_subscriptions_period_order",
        ),
        CheckConstraint(
            "(billing_period_days_snapshot IS NULL OR billing_period_days_snapshot > 0)",
            name="ck_subscriptions_billing_period_snapshot",
        ),
        CheckConstraint(
            "(purchased_price IS NULL AND purchased_currency IS NULL) OR "
            "(purchased_price IS NOT NULL AND purchased_price >= 0 AND "
            " ((purchased_price = 0 AND "
            "   (purchased_currency IS NULL OR length(purchased_currency) = 3)) OR "
            "  (purchased_price > 0 AND purchased_currency IS NOT NULL "
            "   AND length(purchased_currency) = 3)))",
            name="ck_subscriptions_price_snapshot",
        ),
        Index(
            "uq_subscriptions_one_current_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'trialing')"),
        ),
        Index("ix_subscriptions_plan_id", "plan_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_period_days_snapshot: Mapped[int | None] = mapped_column()
    purchased_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    purchased_currency: Mapped[str | None] = mapped_column(String(3))
    entitlement_snapshot: Mapped[JSONValue | None] = mapped_column(JSONB)
