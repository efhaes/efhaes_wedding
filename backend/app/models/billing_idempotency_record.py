from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class BillingIdempotencyRecord(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "billing_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "operation",
            "key_hash",
            name="uq_billing_idempotency_user_operation_key",
        ),
        CheckConstraint(
            "operation IN ('personal_purchase', 'workspace_subscription', 'checkout')",
            name="ck_billing_idempotency_operation",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed')",
            name="ck_billing_idempotency_status",
        ),
        CheckConstraint(
            "(status = 'in_progress' AND personal_purchase_id IS NULL "
            "AND subscription_id IS NULL AND payment_id IS NULL "
            "AND checkout_attempt_id IS NULL) OR "
            "(status = 'completed' AND "
            "((personal_purchase_id IS NOT NULL AND payment_id IS NOT NULL "
            "AND subscription_id IS NULL AND checkout_attempt_id IS NULL) OR "
            "(subscription_id IS NOT NULL AND payment_id IS NOT NULL "
            "AND personal_purchase_id IS NULL AND checkout_attempt_id IS NULL) OR "
            "(checkout_attempt_id IS NOT NULL AND personal_purchase_id IS NULL "
            "AND subscription_id IS NULL AND payment_id IS NULL)))",
            name="ck_billing_idempotency_result",
        ),
        CheckConstraint(
            "replay_count >= 0",
            name="ck_billing_idempotency_replay_count",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress", server_default="in_progress"
    )
    personal_purchase_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("personal_purchases.id", ondelete="RESTRICT"),
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="RESTRICT"),
    )
    payment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
    )
    checkout_attempt_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("checkout_attempts.id", ondelete="RESTRICT"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
