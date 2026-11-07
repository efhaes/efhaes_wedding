from datetime import datetime, timezone
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.payment_webhook_event import PaymentWebhookEvent
from app.modules.billing.repositories.billing_repository import BillingRepository
from app.modules.billing.services.midtrans_client import MidtransError
from app.modules.billing.services.midtrans_security import (
    MidtransNotification,
    verify_notification_signature,
)
from app.modules.billing.services.midtrans_verification_service import (
    MidtransProviderVerifier,
)
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
)
from app.modules.billing.services.provider_event_identity import (
    build_provider_event_id,
)

logger = logging.getLogger(__name__)


class MidtransWebhookService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        verifier: MidtransProviderVerifier,
    ) -> None:
        self.session = session
        self.settings = settings
        self.verifier = verifier
        self.repository = BillingRepository(session)

    def process(self, payload: dict[str, Any]) -> dict[str, bool]:
        try:
            server_key = self.settings.require_midtrans_server_key().get_secret_value()
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Midtrans webhook processing is not configured",
            ) from error
        try:
            notification = verify_notification_signature(payload, server_key)
            event_id = build_provider_event_id("midtrans", payload)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Midtrans notification is invalid",
            ) from error

        payment = self.repository.payment_for_provider_order_id(
            "midtrans", notification.order_id
        )
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment not found",
            )

        event, created = self._claim_event(
            notification, event_id, payload, payment.id
        )
        if not created and event.processing_status in {"processed", "failed"}:
            return {"received": True}
        event.processing_attempts += 1
        self._commit_event()

        try:
            result = self.verifier.verify_webhook_transaction(notification, payment)
        except ValueError:
            self._mark_manual_review(
                event, f"manual_review:{notification.transaction_status}"
            )
            return {"received": True}
        except MidtransError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Midtrans status verification is temporarily unavailable",
                headers={"Retry-After": "30"},
            ) from error

        if result.manual_review_reason is not None:
            self._mark_manual_review(
                event, f"manual_review:{notification.transaction_status}"
            )
            logger.warning(
                "Midtrans notification requires manual review",
                extra={
                    "payment_id": str(payment.id),
                    "provider_order_id": notification.order_id,
                    "provider_event_id": event_id,
                    "provider_status": notification.transaction_status,
                    "reason_code": result.manual_review_reason,
                },
            )
            return {"received": True}

        if result.outcome is not None:
            try:
                PaymentLifecycleService(self.session).record_verified_outcome(
                    payment.id, result.outcome
                )
            except HTTPException as error:
                if error.status_code != status.HTTP_409_CONFLICT:
                    raise
                self._mark_manual_review(
                    event, f"manual_review:{notification.transaction_status}"
                )
                logger.warning(
                    "Midtrans notification conflicts with payment lifecycle",
                    extra={
                        "payment_id": str(payment.id),
                        "provider_order_id": notification.order_id,
                        "provider_event_id": event_id,
                        "provider_status": notification.transaction_status,
                    },
                )
                return {"received": True}

        event.processing_status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        self._commit_event()
        return {"received": True}

    def _claim_event(
        self,
        notification: MidtransNotification,
        event_id: str,
        payload: dict[str, Any],
        payment_id: UUID,
    ) -> tuple[PaymentWebhookEvent, bool]:
        event_payload = {
            key: payload[key]
            for key in (
                "order_id",
                "transaction_id",
                "transaction_status",
                "status_code",
                "gross_amount",
                "currency",
                "fraud_status",
                "payment_type",
                "transaction_time",
                "settlement_time",
            )
            if key in payload and isinstance(payload[key], (str, int, float))
        }
        values = {
            "id": uuid4(),
            "payment_id": payment_id,
            "provider": "midtrans",
            "provider_event_id": event_id,
            "event_type": notification.transaction_status,
            "payload": event_payload,
            "processing_status": "pending",
            "processing_attempts": 0,
        }
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = postgresql_insert(PaymentWebhookEvent).values(**values)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(PaymentWebhookEvent).values(**values)
        else:
            raise RuntimeError(
                f"Webhook event storage is unsupported for {dialect_name}"
            )
        statement = statement.on_conflict_do_nothing(
            index_elements=["provider", "provider_event_id"]
        )
        inserted = self.session.execute(statement).rowcount == 1
        event = self.session.scalar(
            select(PaymentWebhookEvent)
            .where(
                PaymentWebhookEvent.provider == "midtrans",
                PaymentWebhookEvent.provider_event_id == event_id,
            )
            .with_for_update()
        )
        if event is None:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook event could not be recorded",
            )
        if not inserted and event.payment_id != values["payment_id"]:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Webhook event identity conflicts with another payment",
            )
        return event, inserted

    def _mark_manual_review(
        self, event: PaymentWebhookEvent, event_type: str
    ) -> None:
        event.processing_status = "failed"
        event.event_type = event_type[:120]
        event.processed_at = None
        self._commit_event()

    def _commit_event(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook event could not be persisted",
                headers={"Retry-After": "30"},
            ) from error
