from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class CheckoutAttempt(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "checkout_attempts"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_checkout_attempts_payment_id"),
        UniqueConstraint(
            "provider",
            "provider_order_id",
            name="uq_checkout_attempts_provider_order_id",
        ),
        CheckConstraint(
            "status IN ('creating', 'ready', 'indeterminate', 'failed', 'resolved')",
            name="ck_checkout_attempts_status",
        ),
        CheckConstraint(
            "length(trim(provider)) > 0 AND length(provider_order_id) > 0",
            name="ck_checkout_attempts_provider_identity",
        ),
        CheckConstraint("amount >= 0", name="ck_checkout_attempts_amount"),
        CheckConstraint("fencing_generation >= 1", name="ck_checkout_attempts_generation"),
        CheckConstraint("attempt_count >= 1", name="ck_checkout_attempts_attempt_count"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_checkout_attempts_lease_pair",
        ),
        Index(
            "ix_checkout_attempts_recovery",
            "status",
            "next_reconcile_at",
            "lease_until",
        ),
    )

    payment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    provider_response_ciphertext: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckoutAttemptHistory(UUIDPrimaryKey, Base):
    __tablename__ = "checkout_attempt_history"
    __table_args__ = (
        CheckConstraint(
            "from_state IS NULL OR from_state IN "
            "('creating', 'ready', 'indeterminate', 'failed', 'resolved')",
            name="ck_checkout_attempt_history_from_state",
        ),
        CheckConstraint(
            "to_state IN ('creating', 'ready', 'indeterminate', 'failed', 'resolved')",
            name="ck_checkout_attempt_history_to_state",
        ),
        CheckConstraint(
            "event_type IN "
            "('created', 'lease_acquired', 'lease_expired', 'state_changed', "
            "'lease_released')",
            name="ck_checkout_attempt_history_event_type",
        ),
        CheckConstraint(
            "fencing_generation >= 1",
            name="ck_checkout_attempt_history_generation",
        ),
        Index(
            "ix_checkout_attempt_history_attempt_created",
            "attempt_id",
            "created_at",
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("checkout_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    fencing_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
