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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class Payment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "(personal_purchase_id IS NOT NULL AND subscription_id IS NULL) OR "
            "(personal_purchase_id IS NULL AND subscription_id IS NOT NULL)",
            name="ck_payments_exactly_one_target",
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'expired', 'refunded', 'cancelled')",
            name="ck_payments_status",
        ),
        CheckConstraint("amount >= 0", name="ck_payments_amount_nonnegative"),
        Index("ix_payments_personal_purchase_id", "personal_purchase_id"),
        Index("ix_payments_subscription_id", "subscription_id"),
        Index("ix_payments_status", "status"),
        Index(
            "uq_payments_provider_order_id",
            "provider",
            "provider_order_id",
            unique=True,
            postgresql_where=text(
                "provider IS NOT NULL AND provider_order_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_payments_provider_transaction_id",
            "provider",
            "provider_transaction_id",
            unique=True,
            postgresql_where=text(
                "provider IS NOT NULL AND provider_transaction_id IS NOT NULL"
            ),
        ),
    )

    personal_purchase_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("personal_purchases.id", ondelete="RESTRICT"),
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("subscriptions.id", ondelete="RESTRICT")
    )
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(255))
    provider_transaction_id: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
