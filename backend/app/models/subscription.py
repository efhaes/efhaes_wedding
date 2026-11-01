from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class Subscription(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'trialing', 'active', 'past_due', 'cancelled', 'expired')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_subscriptions_period_order",
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
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
