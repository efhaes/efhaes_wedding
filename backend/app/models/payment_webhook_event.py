from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, UUIDPrimaryKey


class PaymentWebhookEvent(UUIDPrimaryKey, Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (Index("ix_payment_webhook_events_payment_id", "payment_id"),)

    payment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
