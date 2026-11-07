import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing_idempotency_record import BillingIdempotencyRecord
from app.models.checkout_attempt import CheckoutAttempt, CheckoutAttemptHistory
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.subscription import Subscription
from app.models.workspace import Workspace
from app.modules.billing.repositories.billing_repository import BillingRepository
from app.modules.billing.services.idempotency import (
    claim_idempotency_record,
    complete_idempotency_record,
    hash_idempotency_key,
    mark_idempotency_replayed,
    request_fingerprint,
)
from app.modules.users.models.user import User


_ORDER_ID_PATTERN = re.compile(r"[A-Za-z0-9._~-]{1,50}\Z")
_REASON_CODE_PATTERN = re.compile(r"[a-z0-9_.-]{1,100}\Z")
_PROVIDER = "midtrans"
_EVIDENCE_SEAL = object()
_READY_RECONCILE_INTERVAL = timedelta(minutes=5)


class CheckoutAttemptEvidenceState(str, Enum):
    READY = "ready"
    FAILED_RETRY_SAFE = "failed"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class CheckoutAttemptVerificationContext:
    attempt_id: UUID
    provider_order_id: str
    fencing_generation: int


@dataclass(frozen=True, slots=True, init=False)
class VerifiedCheckoutAttemptEvidence:
    attempt_id: UUID
    provider_order_id: str
    fencing_generation: int
    state: CheckoutAttemptEvidenceState
    reason_code: str
    _seal: object

    def __init__(
        self,
        attempt_id: UUID,
        provider_order_id: str,
        fencing_generation: int,
        state: CheckoutAttemptEvidenceState,
        reason_code: str,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _EVIDENCE_SEAL:
            raise TypeError("Checkout provider evidence must come from a trusted verifier")
        if not isinstance(state, CheckoutAttemptEvidenceState):
            raise TypeError("Checkout provider evidence state must be typed")
        if not _ORDER_ID_PATTERN.fullmatch(provider_order_id):
            raise ValueError("Provider evidence order ID is invalid")
        if fencing_generation < 1:
            raise ValueError("Provider evidence fencing generation is invalid")
        _validate_reason_code(reason_code)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "provider_order_id", provider_order_id)
        object.__setattr__(self, "fencing_generation", fencing_generation)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "_seal", _seal)


class CheckoutAttemptRetrySafetyVerifier(Protocol):
    def verify_no_active_transaction(
        self, context: CheckoutAttemptVerificationContext
    ) -> VerifiedCheckoutAttemptEvidence | None: ...


def _issue_provider_evidence(
    context: CheckoutAttemptVerificationContext,
    state: CheckoutAttemptEvidenceState,
    reason_code: str,
) -> VerifiedCheckoutAttemptEvidence:
    return VerifiedCheckoutAttemptEvidence(
        context.attempt_id,
        context.provider_order_id,
        context.fencing_generation,
        state,
        reason_code,
        _seal=_EVIDENCE_SEAL,
    )


def build_provider_order_id(payment_id: UUID) -> str:
    order_id = f"pay-{payment_id.hex}"
    if not _ORDER_ID_PATTERN.fullmatch(order_id):
        raise ValueError("Generated provider order ID is invalid")
    return order_id


class CheckoutFoundationService:
    """Persist checkout claims and recovery state without calling a provider."""

    def __init__(
        self,
        session: Session,
        *,
        retry_safety_verifier: CheckoutAttemptRetrySafetyVerifier | None = None,
    ) -> None:
        self.session = session
        self.repository = BillingRepository(session)
        self.retry_safety_verifier = retry_safety_verifier

    def claim_attempt(
        self,
        user: User,
        payment_id: UUID,
        idempotency_key: str,
        *,
        worker_id: str,
        currency: str | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        _validate_lease_duration(lease_duration)
        key_hash = hash_idempotency_key(idempotency_key)

        payment_hint = self.repository.get_payment(payment_id)
        if payment_hint is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
            )
        workspace = self._lock_workspace_and_authorize(user, payment_hint)
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
        target = self._lock_target(payment, workspace, user)
        self._validate_payment_snapshot(payment, target, currency)

        fingerprint = request_fingerprint(
            "checkout",
            {
                "amount": _decimal_text(payment.amount),
                "currency": payment.currency,
                "payment_id": str(payment.id),
                "provider": _PROVIDER,
                "target_id": str(target.id),
            },
        )
        idempotency_record, claimed = claim_idempotency_record(
            self.session,
            user_id=user.id,
            operation="checkout",
            key_hash=key_hash,
            fingerprint=fingerprint,
        )
        if not claimed:
            attempt = self._attempt_from_replay(idempotency_record)
            self._expire_stale_creation_lease(attempt, current_time, worker_id)
            mark_idempotency_replayed(idempotency_record)
            self._commit()
            return attempt

        attempt = self.repository.checkout_attempt_for_payment(
            payment.id, lock=True
        )
        if attempt is not None:
            self._validate_attempt_matches_payment(attempt, payment, fingerprint)
            self._expire_stale_creation_lease(attempt, current_time, worker_id)
            complete_idempotency_record(
                idempotency_record, checkout_attempt_id=attempt.id
            )
            self._commit()
            return attempt

        if payment.status != "pending" or target.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment is not eligible for checkout",
            )
        if payment.provider is not None or payment.provider_order_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment already has provider identity without a checkout attempt",
            )

        order_id = build_provider_order_id(payment.id)
        attempt = CheckoutAttempt(
            id=uuid4(),
            payment_id=payment.id,
            provider=_PROVIDER,
            provider_order_id=order_id,
            request_fingerprint=fingerprint,
            amount=payment.amount,
            currency=payment.currency,
            status="creating",
            lease_owner=worker_id,
            lease_until=current_time + lease_duration,
            fencing_generation=1,
            attempt_count=1,
        )
        payment.provider = _PROVIDER
        payment.provider_order_id = order_id
        self.session.add(attempt)
        self.session.flush()
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state=None,
                to_state="creating",
                event_type="created",
                fencing_generation=attempt.fencing_generation,
                actor=worker_id,
                reason_code="checkout_claimed",
            )
        )
        complete_idempotency_record(
            idempotency_record, checkout_attempt_id=attempt.id
        )
        self._commit()
        return attempt

    def claim_reconciliation_lease(
        self,
        attempt_id: UUID,
        *,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        _validate_lease_duration(lease_duration)
        attempt = self.repository.lock_checkout_attempt(attempt_id)
        if attempt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkout attempt not found",
            )
        if attempt.status == "creating" and _lease_expired(
            attempt.lease_until, current_time
        ):
            self._expire_stale_creation_lease(attempt, current_time, worker_id)
        if attempt.status not in {"ready", "indeterminate"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt is not available for recovery",
            )
        if attempt.lease_until is not None and not _lease_expired(
            attempt.lease_until, current_time
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt is already leased",
                headers={"Retry-After": "1"},
            )
        if attempt.status in {"ready", "indeterminate"} and (
            attempt.next_reconcile_at is not None
            and _as_utc(attempt.next_reconcile_at) > current_time
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout reconciliation is not due",
            )

        previous_generation = attempt.fencing_generation
        attempt.fencing_generation += 1
        attempt.attempt_count += 1
        attempt.lease_owner = worker_id
        attempt.lease_until = current_time + lease_duration
        attempt.next_reconcile_at = None
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state=attempt.status,
                to_state=attempt.status,
                event_type="lease_acquired",
                fencing_generation=attempt.fencing_generation,
                actor=worker_id,
                reason_code=f"reclaimed_generation_{previous_generation}",
            )
        )
        self._commit()
        return attempt

    def mark_indeterminate(
        self,
        attempt_id: UUID,
        *,
        worker_id: str,
        fencing_generation: int,
        reason_code: str,
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        _validate_reason_code(reason_code)
        attempt = self._locked_owned_attempt(
            attempt_id, worker_id, fencing_generation, current_time
        )
        if attempt.status not in {"creating", "ready"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt state transition is not allowed",
            )
        return self._persist_state_transition(
            attempt,
            worker_id=worker_id,
            fencing_generation=fencing_generation,
            expected_state=attempt.status,
            to_state="indeterminate",
            reason_code=reason_code,
            now=current_time,
        )

    def apply_verified_provider_evidence(
        self,
        attempt_id: UUID,
        *,
        worker_id: str,
        fencing_generation: int,
        evidence: VerifiedCheckoutAttemptEvidence,
        provider_response_ciphertext: str | None = None,
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        if (
            not isinstance(evidence, VerifiedCheckoutAttemptEvidence)
            or evidence._seal is not _EVIDENCE_SEAL
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Trusted provider verification evidence is required",
            )
        attempt = self._locked_owned_attempt(
            attempt_id, worker_id, fencing_generation, current_time
        )
        if (
            evidence.attempt_id != attempt.id
            or evidence.provider_order_id != attempt.provider_order_id
            or evidence.fencing_generation != fencing_generation
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider evidence does not match the current checkout attempt",
            )
        allowed = {
            "creating": {
                CheckoutAttemptEvidenceState.READY,
                CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE,
            },
            "ready": {CheckoutAttemptEvidenceState.RESOLVED},
            "indeterminate": {
                CheckoutAttemptEvidenceState.READY,
                CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE,
                CheckoutAttemptEvidenceState.RESOLVED,
            },
            "failed": set(),
            "resolved": set(),
        }
        if evidence.state not in allowed[attempt.status]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt state transition is not allowed",
            )
        if evidence.state is CheckoutAttemptEvidenceState.READY and (
            not isinstance(provider_response_ciphertext, str)
            or not provider_response_ciphertext
            or len(provider_response_ciphertext) > 16384
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A valid encrypted provider checkout response is required",
            )
        if (
            evidence.state is not CheckoutAttemptEvidenceState.READY
            and provider_response_ciphertext is not None
        ):
            raise ValueError(
                "Provider checkout response is only valid for a ready attempt"
            )
        return self._persist_state_transition(
            attempt,
            worker_id=worker_id,
            fencing_generation=fencing_generation,
            expected_state=attempt.status,
            to_state=evidence.state.value,
            reason_code=evidence.reason_code,
            now=current_time,
            extra_values=(
                {"provider_response_ciphertext": provider_response_ciphertext}
                if evidence.state is CheckoutAttemptEvidenceState.READY
                else None
            ),
        )

    def retry_failed_attempt(
        self,
        attempt_id: UUID,
        *,
        worker_id: str,
        fencing_generation: int,
        lease_duration: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        _validate_lease_duration(lease_duration)
        attempt_snapshot = self.session.get(CheckoutAttempt, attempt_id)
        if attempt_snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkout attempt not found",
            )
        if (
            attempt_snapshot.status != "failed"
            or attempt_snapshot.fencing_generation != fencing_generation
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt is no longer eligible for retry",
            )
        if self.retry_safety_verifier is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider retry safety is unverified; reconciliation is required",
            )
        context = CheckoutAttemptVerificationContext(
            attempt_id=attempt_snapshot.id,
            provider_order_id=attempt_snapshot.provider_order_id,
            fencing_generation=attempt_snapshot.fencing_generation,
        )
        evidence = self.retry_safety_verifier.verify_no_active_transaction(context)
        if (
            not isinstance(evidence, VerifiedCheckoutAttemptEvidence)
            or evidence._seal is not _EVIDENCE_SEAL
            or evidence.state is not CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE
            or evidence.attempt_id != context.attempt_id
            or evidence.provider_order_id != context.provider_order_id
            or evidence.fencing_generation != context.fencing_generation
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Provider verification did not prove that retry is safe; "
                    "reconciliation is required"
                ),
            )
        attempt = self.repository.lock_checkout_attempt(attempt_id)
        if attempt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkout attempt not found",
            )
        if attempt.status != "failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only a failed checkout attempt can be retried",
            )
        if (
            evidence.attempt_id != attempt.id
            or evidence.provider_order_id != attempt.provider_order_id
            or attempt_snapshot.status != attempt.status
            or attempt_snapshot.provider_order_id != attempt.provider_order_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider evidence does not match the checkout attempt",
            )
        previous_generation = attempt.fencing_generation
        if previous_generation != fencing_generation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt fencing generation has changed",
            )
        result = self.session.execute(
            update(CheckoutAttempt)
            .where(
                CheckoutAttempt.id == attempt.id,
                CheckoutAttempt.status == "failed",
                CheckoutAttempt.fencing_generation == previous_generation,
                CheckoutAttempt.lease_owner.is_(None),
                CheckoutAttempt.lease_until.is_(None),
            )
            .values(
                status="creating",
                fencing_generation=previous_generation + 1,
                attempt_count=CheckoutAttempt.attempt_count + 1,
                lease_owner=worker_id,
                lease_until=current_time + lease_duration,
                next_reconcile_at=None,
                last_error_code=None,
                provider_response_ciphertext=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt changed before retry could be claimed",
            )
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state="failed",
                to_state="creating",
                event_type="state_changed",
                fencing_generation=previous_generation + 1,
                actor=worker_id,
                reason_code=f"safe_retry_generation_{previous_generation}",
            )
        )
        self._commit()
        self.session.refresh(attempt)
        return attempt

    def release_reconciliation_lease(
        self,
        attempt_id: UUID,
        *,
        worker_id: str,
        fencing_generation: int,
        next_reconcile_at: datetime,
        reason_code: str,
        now: datetime | None = None,
    ) -> CheckoutAttempt:
        current_time = _utc_now(now)
        _validate_worker_id(worker_id)
        _validate_reason_code(reason_code)
        attempt = self._locked_owned_attempt(
            attempt_id, worker_id, fencing_generation, current_time
        )
        if attempt.status not in {"ready", "indeterminate"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt is not available for reconciliation release",
            )
        scheduled_time = _as_utc(next_reconcile_at)
        if scheduled_time <= current_time:
            raise ValueError("The next reconciliation time must be in the future")
        attempt.lease_owner = None
        attempt.lease_until = None
        attempt.next_reconcile_at = scheduled_time
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state=attempt.status,
                to_state=attempt.status,
                event_type="lease_released",
                fencing_generation=attempt.fencing_generation,
                actor=worker_id,
                reason_code=reason_code,
            )
        )
        self._commit()
        return attempt

    def _lock_workspace_and_authorize(
        self, user: User, payment_hint: Payment
    ) -> Workspace | None:
        if payment_hint.personal_purchase_id is not None:
            return None
        if payment_hint.subscription_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment target is inconsistent",
            )
        workspace_id = self.repository.subscription_workspace_id(
            payment_hint.subscription_id
        )
        if workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment target is inconsistent",
            )
        workspace = self.repository.lock_workspace(workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace is not active",
            )
        if self.repository.active_billing_membership(workspace_id, user.id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment not found",
            )
        return workspace

    def _lock_target(
        self,
        payment: Payment,
        workspace: Workspace | None,
        user: User,
    ) -> PersonalPurchase | Subscription:
        if payment.personal_purchase_id is not None:
            target = self.repository.lock_purchase(payment.personal_purchase_id)
            if target is None or target.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Payment not found",
                )
            return target
        if payment.subscription_id is not None and workspace is not None:
            target = self.repository.lock_subscription(payment.subscription_id)
            if target is not None and target.workspace_id == workspace.id:
                return target
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment target is inconsistent",
        )

    @staticmethod
    def _validate_payment_snapshot(
        payment: Payment,
        target: PersonalPurchase | Subscription,
        expected_currency: str | None,
    ) -> None:
        if (
            payment.amount <= Decimal("0")
            or payment.amount != payment.amount.to_integral_value()
            or payment.currency is None
            or len(payment.currency) != 3
            or not payment.currency.isascii()
            or not payment.currency.isalpha()
            or (
                expected_currency is not None
                and payment.currency != expected_currency
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment amount or currency is not valid for checkout",
            )
        if (
            payment.amount != target.purchased_price
            or payment.currency != target.purchased_currency
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment does not match the transaction snapshot",
            )

    @staticmethod
    def _validate_attempt_matches_payment(
        attempt: CheckoutAttempt,
        payment: Payment,
        fingerprint: str,
    ) -> None:
        if (
            attempt.payment_id != payment.id
            or attempt.provider != _PROVIDER
            or attempt.request_fingerprint != fingerprint
            or attempt.provider_order_id != payment.provider_order_id
            or payment.provider != _PROVIDER
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt conflicts with the payment identity",
            )

    def _attempt_from_replay(
        self, record: BillingIdempotencyRecord
    ) -> CheckoutAttempt:
        if record.checkout_attempt_id is None:
            raise RuntimeError("Completed checkout idempotency record is incomplete")
        attempt = self.repository.lock_checkout_attempt(record.checkout_attempt_id)
        if attempt is None:
            raise RuntimeError("Completed checkout idempotency result is missing")
        return attempt

    def _expire_stale_creation_lease(
        self,
        attempt: CheckoutAttempt,
        now: datetime,
        actor: str,
    ) -> None:
        if attempt.status != "creating" or not _lease_expired(
            attempt.lease_until, now
        ):
            return
        generation = attempt.fencing_generation
        attempt.status = "indeterminate"
        attempt.lease_owner = None
        attempt.lease_until = None
        attempt.next_reconcile_at = now
        attempt.last_error_code = "creation_lease_expired"
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state="creating",
                to_state="indeterminate",
                event_type="lease_expired",
                fencing_generation=generation,
                actor=actor,
                reason_code="creation_lease_expired",
            )
        )

    def _locked_owned_attempt(
        self,
        attempt_id: UUID,
        worker_id: str,
        fencing_generation: int,
        now: datetime,
    ) -> CheckoutAttempt:
        attempt = self.repository.lock_checkout_attempt(attempt_id)
        if attempt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkout attempt not found",
            )
        if (
            attempt.lease_owner != worker_id
            or attempt.fencing_generation != fencing_generation
            or _lease_expired(attempt.lease_until, now)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout worker lease is stale",
            )
        return attempt

    def _persist_state_transition(
        self,
        attempt: CheckoutAttempt,
        *,
        worker_id: str,
        fencing_generation: int,
        expected_state: str,
        to_state: str,
        reason_code: str,
        now: datetime,
        extra_values: dict[str, object] | None = None,
    ) -> CheckoutAttempt:
        values: dict[str, object] = {
            "status": to_state,
            "last_error_code": reason_code if to_state in {"failed", "indeterminate"} else None,
            "lease_owner": None,
            "lease_until": None,
        }
        if to_state == "indeterminate":
            values["next_reconcile_at"] = now
        elif to_state == "ready":
            values["next_reconcile_at"] = now + _READY_RECONCILE_INTERVAL
        elif to_state == "resolved":
            values["resolved_at"] = now
            values["next_reconcile_at"] = None
            values["provider_response_ciphertext"] = None
        elif to_state == "failed":
            values["provider_response_ciphertext"] = None
        if extra_values:
            values.update(extra_values)

        result = self.session.execute(
            update(CheckoutAttempt)
            .where(
                CheckoutAttempt.id == attempt.id,
                CheckoutAttempt.status == expected_state,
                CheckoutAttempt.lease_owner == worker_id,
                CheckoutAttempt.fencing_generation == fencing_generation,
                CheckoutAttempt.lease_until > now,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout attempt lease or state changed before transition",
            )
        self.session.add(
            CheckoutAttemptHistory(
                attempt_id=attempt.id,
                from_state=expected_state,
                to_state=to_state,
                event_type="state_changed",
                fencing_generation=fencing_generation,
                actor=worker_id,
                reason_code=reason_code,
            )
        )
        self._commit()
        self.session.refresh(attempt)
        return attempt

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout state conflicts with an existing transaction",
            ) from error


def _validate_worker_id(worker_id: str) -> None:
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 128:
        raise ValueError("Checkout worker ID must contain 1 to 128 characters")


def _validate_lease_duration(duration: timedelta) -> None:
    if duration <= timedelta(0) or duration > timedelta(minutes=15):
        raise ValueError("Checkout lease duration must be between 0 and 15 minutes")


def _validate_reason_code(reason_code: str) -> None:
    if not isinstance(reason_code, str) or not _REASON_CODE_PATTERN.fullmatch(reason_code):
        raise ValueError("Checkout reason code must be a sanitized code")


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Checkout timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lease_expired(lease_until: datetime | None, now: datetime) -> bool:
    return lease_until is None or _as_utc(lease_until) <= now


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
