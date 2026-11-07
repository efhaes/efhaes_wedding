from datetime import datetime
import re
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base, JSONValue, UUIDPrimaryKey


class PaymentWebhookEvent(UUIDPrimaryKey, Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('pending', 'processed', 'failed')",
            name="ck_payment_webhook_events_processing_status",
        ),
        CheckConstraint(
            "processing_attempts >= 0",
            name="ck_payment_webhook_events_processing_attempts",
        ),
        CheckConstraint(
            "provider IS NULL OR length(trim(provider)) > 0",
            name="ck_payment_webhook_events_provider_nonempty",
        ),
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_payment_webhook_events_provider_event_id",
        ),
        Index(
            "uq_payment_webhook_events_legacy_event_id",
            "provider_event_id",
            unique=True,
            postgresql_where=text("provider IS NULL"),
            sqlite_where=text("provider IS NULL"),
        ),
        Index("ix_payment_webhook_events_payment_id", "payment_id"),
    )

    payment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    processing_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_PROVIDER_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


@event.listens_for(PaymentWebhookEvent, "before_insert")
def validate_new_payment_webhook_event(
    _mapper: Mapper[PaymentWebhookEvent],
    _connection: Connection,
    target: PaymentWebhookEvent,
) -> None:
    provider = target.provider
    if not isinstance(provider, str):
        raise ValueError("New payment webhook events require a provider")
    normalized_provider = provider.strip().lower()
    if not _PROVIDER_ID_PATTERN.fullmatch(normalized_provider):
        raise ValueError("Payment webhook event provider is invalid")
    target.provider = normalized_provider

    if (
        not isinstance(target.provider_event_id, str)
        or not target.provider_event_id.strip()
        or len(target.provider_event_id) > 255
    ):
        raise ValueError("Payment webhook event ID is invalid")
    target.provider_event_id = target.provider_event_id.strip()
