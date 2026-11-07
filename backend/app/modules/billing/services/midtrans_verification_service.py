from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.core.config import Settings
from app.models.checkout_attempt import CheckoutAttempt
from app.models.payment import Payment
from app.modules.billing.services.midtrans_client import (
    MidtransTransactionStatus,
)
from app.modules.billing.services.midtrans_security import MidtransNotification
from app.modules.billing.services.payment_lifecycle_service import (
    VerifiedPaymentOutcome,
    _issue_verified_payment_outcome,
)


class MidtransStatusClient(Protocol):
    def get_transaction_status(
        self, *, order_id: str, transaction_id: str | None = None
    ) -> MidtransTransactionStatus: ...


@dataclass(frozen=True, slots=True)
class MidtransVerificationResult:
    transaction: MidtransTransactionStatus
    outcome: VerifiedPaymentOutcome | None
    manual_review_reason: str | None = None


class MidtransProviderVerifier:
    def __init__(
        self,
        settings: Settings,
        client: MidtransStatusClient,
    ) -> None:
        self.settings = settings
        self.client = client

    def verify_webhook_transaction(
        self,
        notification: MidtransNotification,
        payment: Payment,
    ) -> MidtransVerificationResult:
        self._validate_payment_snapshot(
            payment,
            notification.order_id,
            notification.gross_amount,
            notification.currency,
        )
        if (
            payment.provider_transaction_id is not None
            and payment.provider_transaction_id != notification.transaction_id
        ):
            raise ValueError("Midtrans transaction identity does not match payment")
        transaction = self.client.get_transaction_status(
            order_id=notification.order_id,
            transaction_id=notification.transaction_id,
        )
        self._validate_status_snapshot(
            transaction,
            expected_order_id=payment.provider_order_id,
            expected_amount=payment.amount,
            expected_currency=payment.currency,
            expected_transaction_id=notification.transaction_id,
        )
        return self._result(transaction, payment)

    def reconcile_checkout_attempt(
        self,
        payment: Payment,
        attempt: CheckoutAttempt,
        *,
        worker_id: str,
        fencing_generation: int,
    ) -> MidtransVerificationResult:
        self._validate_payment_snapshot(
            payment,
            attempt.provider_order_id,
            attempt.amount,
            attempt.currency,
        )
        transaction = self.client.get_transaction_status(
            order_id=attempt.provider_order_id,
            transaction_id=payment.provider_transaction_id,
        )
        self._validate_status_snapshot(
            transaction,
            expected_order_id=attempt.provider_order_id,
            expected_amount=attempt.amount,
            expected_currency=attempt.currency,
            expected_transaction_id=payment.provider_transaction_id,
        )
        return self._result(
            transaction,
            payment,
            attempt_id=attempt.id,
            checkout_worker_id=worker_id,
            fencing_generation=fencing_generation,
        )

    def _result(
        self,
        transaction: MidtransTransactionStatus,
        payment: Payment,
        *,
        attempt_id: UUID | None = None,
        checkout_worker_id: str | None = None,
        fencing_generation: int | None = None,
    ) -> MidtransVerificationResult:
        decision = _map_transaction_status(
            transaction.transaction_status,
            transaction.fraud_status,
        )
        if decision is None:
            return MidtransVerificationResult(
                transaction=transaction,
                outcome=None,
                manual_review_reason=(
                    f"unsupported_or_unsafe_status_{transaction.transaction_status}"
                ),
            )
        if decision == "pending":
            if payment.status != "pending":
                return MidtransVerificationResult(
                    transaction=transaction,
                    outcome=None,
                    manual_review_reason="provider_status_regressed_to_pending",
                )
            return MidtransVerificationResult(
                transaction=transaction,
                outcome=_issue_verified_payment_outcome(
                    "pending",
                    paid_at=None,
                    provider="midtrans",
                    provider_order_id=transaction.order_id,
                    provider_transaction_id=transaction.transaction_id,
                    attempt_id=attempt_id,
                    checkout_worker_id=checkout_worker_id,
                    fencing_generation=fencing_generation,
                ),
            )
        if (
            transaction.transaction_status == "deny"
            and payment.status == "paid"
        ):
            return MidtransVerificationResult(
                transaction=transaction,
                outcome=None,
                manual_review_reason="settled_transaction_reversed_to_deny",
            )
        return MidtransVerificationResult(
            transaction=transaction,
            outcome=_issue_verified_payment_outcome(
                decision,
                paid_at=(
                    datetime.now(timezone.utc) if decision == "paid" else None
                ),
                provider="midtrans",
                provider_order_id=transaction.order_id,
                provider_transaction_id=transaction.transaction_id,
                attempt_id=attempt_id,
                checkout_worker_id=checkout_worker_id,
                fencing_generation=fencing_generation,
            ),
        )

    def _validate_payment_snapshot(
        self,
        payment: Payment,
        order_id: str,
        amount: Decimal,
        currency: str,
    ) -> None:
        if (
            payment.provider != "midtrans"
            or payment.provider_order_id != order_id
            or payment.amount != amount
            or payment.currency != currency
            or payment.currency != self.settings.require_midtrans_currency()
        ):
            raise ValueError("Midtrans transaction does not match payment snapshot")

    def _validate_status_snapshot(
        self,
        transaction: MidtransTransactionStatus,
        *,
        expected_order_id: str | None,
        expected_amount: Decimal,
        expected_currency: str,
        expected_transaction_id: str | None,
    ) -> None:
        if (
            transaction.order_id != expected_order_id
            or transaction.gross_amount != expected_amount
            or transaction.currency != expected_currency
            or transaction.currency != self.settings.require_midtrans_currency()
            or (
                expected_transaction_id is not None
                and transaction.transaction_id != expected_transaction_id
            )
        ):
            raise ValueError("Midtrans status identity does not match payment snapshot")


def _map_transaction_status(
    transaction_status: str,
    fraud_status: str | None,
) -> str | None:
    if transaction_status == "pending":
        return "pending"
    if transaction_status in {"capture", "settlement"}:
        if fraud_status != "accept":
            return None
        return "paid"
    if transaction_status in {"deny", "failure"}:
        return "failed"
    if transaction_status == "cancel":
        return "cancelled"
    if transaction_status == "expire":
        return "expired"
    if transaction_status == "refund":
        return "refunded"
    if transaction_status in {
        "partial_refund",
        "chargeback",
        "partial_chargeback",
        "authorize",
    }:
        return None
    return None
