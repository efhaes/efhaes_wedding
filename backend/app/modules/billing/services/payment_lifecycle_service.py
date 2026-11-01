from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.subscription import Subscription
from app.models.workspace import Workspace
from app.modules.billing.repositories.billing_repository import BillingRepository


VerifiedPaymentStatus = Literal["paid", "failed", "expired", "cancelled", "refunded"]


@dataclass(frozen=True)
class VerifiedPaymentOutcome:
    status: VerifiedPaymentStatus
    paid_at: datetime | None = None
    provider: str | None = None
    provider_reference: str | None = None


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
        payment = self.repository.lock_payment(payment_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
            )

        if payment.status == outcome.status:
            return payment
        if payment.status == "paid" and outcome.status == "refunded":
            return self._refund(payment)
        if outcome.status == "refunded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only a paid payment can be refunded",
            )
        if payment.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment status transition is not allowed",
            )

        target = self._lock_target(payment)
        if outcome.status == "paid":
            self._activate(payment, target, outcome)
        else:
            self._close_pending_target(target, outcome.status)
        payment.status = outcome.status
        payment.paid_at = outcome.paid_at if outcome.status == "paid" else None
        if outcome.provider_reference is not None:
            payment.provider_reference = outcome.provider_reference
        if outcome.provider is not None:
            payment.provider = outcome.provider
        self._commit()
        return payment

    @staticmethod
    def _validate_outcome(outcome: VerifiedPaymentOutcome) -> None:
        if outcome.status not in {
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

    def _lock_target(self, payment: Payment) -> PersonalPurchase | Subscription:
        if payment.personal_purchase_id is not None:
            target = self.repository.lock_purchase(payment.personal_purchase_id)
        elif payment.subscription_id is not None:
            target = self.repository.lock_subscription(payment.subscription_id)
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
        workspace = self.session.scalar(
            select(Workspace)
            .where(Workspace.id == target.workspace_id)
            .with_for_update(of=Workspace)
        )
        if workspace is None or workspace.status != "active":
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

    def _refund(self, payment: Payment) -> Payment:
        target = self._lock_target(payment)
        if target.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an active transaction can be refunded",
            )
        target.status = "refunded"
        payment.status = "refunded"
        self._commit()
        return payment

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
