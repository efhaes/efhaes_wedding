from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.checkout_attempt import CheckoutAttempt, CheckoutAttemptHistory
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.subscription import Subscription
from app.models.workspace import Workspace
from app.modules.billing.repositories.billing_repository import BillingRepository


VerifiedPaymentStatus = Literal[
    "pending", "paid", "failed", "expired", "cancelled", "refunded"
]
_VERIFIED_OUTCOME_SEAL = object()


@dataclass(frozen=True, slots=True)
class VerifiedPaymentOutcome:
    status: VerifiedPaymentStatus
    paid_at: datetime | None = None
    provider: str | None = None
    provider_reference: str | None = None
    provider_order_id: str | None = None
    provider_transaction_id: str | None = None
    attempt_id: UUID | None = None
    checkout_worker_id: str | None = None
    fencing_generation: int | None = None
    _seal: object | None = field(default=None, repr=False, compare=False)


def _issue_verified_payment_outcome(
    status_value: VerifiedPaymentStatus,
    *,
    paid_at: datetime | None,
    provider: str,
    provider_order_id: str,
    provider_transaction_id: str,
    attempt_id: UUID | None = None,
    checkout_worker_id: str | None = None,
    fencing_generation: int | None = None,
) -> VerifiedPaymentOutcome:
    return VerifiedPaymentOutcome(
        status=status_value,
        paid_at=paid_at,
        provider=provider,
        provider_reference=provider_transaction_id,
        provider_order_id=provider_order_id,
        provider_transaction_id=provider_transaction_id,
        attempt_id=attempt_id,
        checkout_worker_id=checkout_worker_id,
        fencing_generation=fencing_generation,
        _seal=_VERIFIED_OUTCOME_SEAL,
    )


class PaymentLifecycleService:
    """Apply outcomes only after a trusted internal provider-verification step."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = BillingRepository(session)

    def record_verified_outcome(
        self,
        payment_id: UUID,
        outcome: VerifiedPaymentOutcome,
    ) -> Payment:
        self._validate_outcome(outcome)

        payment_hint = self.repository.get_payment(payment_id)
        if payment_hint is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
            )
        workspace: Workspace | None = None
        if payment_hint.subscription_id is not None:
            workspace_id = self.repository.subscription_workspace_id(
                payment_hint.subscription_id
            )
            if workspace_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Payment target is inconsistent",
                )
            workspace = self.repository.lock_workspace(workspace_id)
            if workspace is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Payment target is inconsistent",
                )

        payment = self.repository.lock_payment(payment_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
            )
        if (
            payment.personal_purchase_id != payment_hint.personal_purchase_id
            or payment.subscription_id != payment_hint.subscription_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment target changed while processing",
            )

        if outcome.provider == "midtrans" and (
            payment.provider != "midtrans"
            or payment.provider_order_id != outcome.provider_order_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Verified provider identity does not match the payment",
            )
        target: PersonalPurchase | Subscription | None = None
        if (
            payment.status == outcome.status
            or (payment.status == "paid" and outcome.status == "refunded")
            or (payment.status == "pending" and outcome.status != "refunded")
        ):
            target = self._lock_target(payment, workspace)
        elif outcome.status == "refunded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only a paid payment can be refunded",
            )
        elif payment.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment status transition is not allowed",
            )

        attempt = self.repository.checkout_attempt_for_payment(
            payment.id, lock=True
        )
        if outcome.attempt_id is not None:
            if (
                attempt is None
                or attempt.id != outcome.attempt_id
                or attempt.payment_id != payment.id
                or attempt.lease_owner != outcome.checkout_worker_id
                or attempt.fencing_generation != outcome.fencing_generation
                or attempt.status not in {"ready", "indeterminate"}
                or attempt.lease_until is None
                or attempt.lease_until <= datetime.now(timezone.utc)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Checkout reconciliation lease is stale",
                )

        if payment.status == outcome.status:
            self._apply_provider_identity(payment, outcome)
            if outcome.status != "pending":
                self._resolve_checkout_attempt(attempt, outcome)
            self._commit()
            return payment
        if payment.status == "paid" and outcome.status == "refunded":
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Payment target is inconsistent",
                )
            self._refund(payment, target)
            self._apply_provider_identity(payment, outcome)
            self._resolve_checkout_attempt(attempt, outcome)
            self._commit()
            return payment
        if outcome.status == "refunded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only a paid payment can be refunded",
            )
        if payment.status != "pending" or target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment status transition is not allowed",
            )
        if outcome.status == "paid":
            self._activate(payment, target, outcome, workspace)
        else:
            self._close_pending_target(target, outcome.status)
        payment.status = outcome.status
        payment.paid_at = outcome.paid_at if outcome.status == "paid" else None
        if outcome.provider_reference is not None:
            payment.provider_reference = outcome.provider_reference
        self._apply_provider_identity(payment, outcome)
        self._resolve_checkout_attempt(attempt, outcome)
        self._commit()
        return payment

    @staticmethod
    def _validate_outcome(outcome: VerifiedPaymentOutcome) -> None:
        if not isinstance(outcome, VerifiedPaymentOutcome):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Verified payment outcome is required",
            )
        if outcome.provider == "midtrans" and outcome._seal is not _VERIFIED_OUTCOME_SEAL:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Trusted Midtrans verification is required",
            )
        if outcome.status not in {
            "pending",
            "paid",
            "failed",
            "expired",
            "cancelled",
            "refunded",
        }:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Payment outcome status is invalid",
            )
        if outcome.provider == "midtrans" and (
            not outcome.provider_order_id
            or not outcome.provider_transaction_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Verified Midtrans identity is required",
            )
        has_attempt_context = any(
            value is not None
            for value in (
                outcome.attempt_id,
                outcome.checkout_worker_id,
                outcome.fencing_generation,
            )
        )
        if has_attempt_context and (
            outcome.attempt_id is None
            or not outcome.checkout_worker_id
            or outcome.fencing_generation is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Checkout reconciliation evidence is incomplete",
            )
        if outcome.status == "paid":
            if outcome.paid_at is None or outcome.paid_at.tzinfo is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Verified paid outcomes require a timezone-aware paid_at",
                )
        elif outcome.paid_at is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Only paid outcomes may include paid_at",
            )
        if (
            outcome.provider_reference is not None
            and len(outcome.provider_reference) > 255
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Provider reference is too long",
            )
        if outcome.provider is not None and (
            not outcome.provider.strip() or len(outcome.provider) > 80
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Payment provider is invalid",
            )

    def _lock_target(
        self, payment: Payment, workspace: Workspace | None
    ) -> PersonalPurchase | Subscription:
        if payment.personal_purchase_id is not None:
            target = self.repository.lock_purchase(payment.personal_purchase_id)
        elif payment.subscription_id is not None and workspace is not None:
            target = self.repository.lock_subscription(payment.subscription_id)
            if target is not None and target.workspace_id != workspace.id:
                target = None
        else:
            target = None
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment target is inconsistent",
            )
        return target

    def _activate(
        self,
        payment: Payment,
        target: PersonalPurchase | Subscription,
        outcome: VerifiedPaymentOutcome,
        workspace: Workspace | None,
    ) -> None:
        if target.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction is no longer pending",
            )
        if self.repository.has_other_paid_payment(payment):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction already has a successful payment",
            )
        if payment.amount != target.purchased_price or (
            payment.currency != target.purchased_currency
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment amount does not match the transaction snapshot",
            )

        paid_at = outcome.paid_at
        if paid_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Verified paid outcomes require paid_at",
            )
        paid_at = paid_at.astimezone(timezone.utc)

        if isinstance(target, PersonalPurchase):
            target.activated_at = paid_at
            try:
                target.expires_at = target.expiration_at(paid_at)
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Purchase duration snapshot is invalid",
                ) from error
            target.status = "active"
            return

        if (
            target.billing_period_days_snapshot is None
            or target.billing_period_days_snapshot <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Subscription billing period snapshot is invalid",
            )
        if (
            workspace is None
            or workspace.id != target.workspace_id
            or workspace.status != "active"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace is not active",
            )
        now = datetime.now(timezone.utc)
        expired_subscriptions: list[Subscription] = []
        for existing in self.repository.active_or_pending_subscriptions(
            workspace.id
        ):
            if existing.id == target.id:
                continue
            if (
                existing.current_period_end is not None
                and existing.current_period_end <= now
            ):
                expired_subscriptions.append(existing)
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Workspace already has a current subscription",
                )
        for existing in expired_subscriptions:
            existing.status = "expired"
            existing.current_period_start = None
            existing.current_period_end = None
        self._flush()
        target.current_period_start = paid_at
        target.current_period_end = paid_at + timedelta(
            days=target.billing_period_days_snapshot
        )
        target.status = "active"

    def _close_pending_target(
        self,
        target: PersonalPurchase | Subscription,
        outcome_status: VerifiedPaymentStatus,
    ) -> None:
        if target.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction is no longer pending",
            )
        target.status = (
            "expired" if isinstance(target, Subscription) and outcome_status == "expired"
            else "cancelled"
        )

    def _refund(
        self,
        payment: Payment,
        target: PersonalPurchase | Subscription,
    ) -> None:
        if target.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an active transaction can be refunded",
            )
        target.status = "refunded"
        if isinstance(target, Subscription):
            target.current_period_start = None
            target.current_period_end = None
        payment.status = "refunded"

    def _apply_provider_identity(
        self, payment: Payment, outcome: VerifiedPaymentOutcome
    ) -> None:
        if outcome.provider is not None:
            payment.provider = outcome.provider
        if outcome.provider_order_id is not None:
            payment.provider_order_id = outcome.provider_order_id
        if outcome.provider_transaction_id is not None:
            payment.provider_transaction_id = outcome.provider_transaction_id
        if outcome.provider_reference is not None:
            payment.provider_reference = outcome.provider_reference

    def _resolve_checkout_attempt(
        self,
        attempt: CheckoutAttempt | None,
        outcome: VerifiedPaymentOutcome,
    ) -> None:
        if attempt is None or attempt.status == "resolved":
            return
        now = datetime.now(timezone.utc)
        previous_state = attempt.status
        previous_generation = attempt.fencing_generation
        attempt.status = "resolved"
        attempt.resolved_at = now
        attempt.lease_owner = None
        attempt.lease_until = None
        attempt.next_reconcile_at = None
        attempt.provider_response_ciphertext = None
        attempt.fencing_generation += 1
        attempt.last_error_code = None
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state=previous_state,
                to_state="resolved",
                event_type="state_changed",
                fencing_generation=attempt.fencing_generation,
                actor=outcome.checkout_worker_id or "midtrans-webhook",
                reason_code=f"payment_{outcome.status}_generation_{previous_generation}",
            )
        )

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment outcome conflicts with the current transaction state",
            ) from error

    def _flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment outcome conflicts with the current transaction state",
            ) from error
