from datetime import datetime, timedelta, timezone
from collections.abc import Callable
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.checkout_attempt import CheckoutAttempt
from app.modules.billing.schemas.transactions import CheckoutResponse
from app.modules.billing.repositories.billing_repository import BillingRepository
from app.modules.billing.services.checkout_foundation_service import (
    CheckoutAttemptEvidenceState,
    CheckoutAttemptVerificationContext,
    CheckoutFoundationService,
    _issue_provider_evidence,
)
from app.modules.billing.services.midtrans_client import (
    MidtransError,
    MidtransHTTPError,
    MidtransMalformedResponse,
    MidtransNetworkError,
    MidtransNotFound,
    MidtransSnapClient,
    MidtransTimeout,
)
from app.modules.billing.services.midtrans_security import (
    decrypt_checkout_response,
    encrypt_checkout_response,
)
from app.modules.billing.services.midtrans_verification_service import (
    MidtransProviderVerifier,
)
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
)
from app.modules.users.models.user import User


_RECONCILE_DELAY = timedelta(seconds=30)
logger = logging.getLogger(__name__)


class MidtransCheckoutService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], MidtransSnapClient] = MidtransSnapClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repository = BillingRepository(session)
        self.foundation = CheckoutFoundationService(session)
        self.client_factory = client_factory

    def reconcile_due_attempts(self, *, limit: int = 100) -> int:
        if limit < 1 or limit > 1000:
            raise ValueError("Reconciliation batch size must be between 1 and 1000")
        self._validate_configuration()
        if self.settings.require_midtrans_currency() != "IDR":
            raise RuntimeError("Configured Midtrans merchant currency is not supported")
        attempt_ids = self.repository.due_checkout_attempt_ids(
            datetime.now(timezone.utc), limit=limit
        )
        for attempt_id in attempt_ids:
            attempt = self.repository.lock_checkout_attempt(attempt_id)
            if attempt is None:
                continue
            self._reconcile(attempt, uuid4().hex)
        return len(attempt_ids)

    def checkout(
        self,
        user: User,
        payment_id: UUID,
        idempotency_key: str,
        *,
        worker_id: str | None = None,
    ) -> tuple[CheckoutResponse, int]:
        self._validate_configuration()
        try:
            currency = self.settings.require_midtrans_currency()
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Midtrans checkout is not configured",
            ) from error
        if currency != "IDR":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Configured Midtrans merchant currency is not supported",
            )
        current_worker = worker_id or uuid4().hex
        attempt = self.foundation.claim_attempt(
            user,
            payment_id,
            idempotency_key,
            worker_id=current_worker,
            currency=currency,
        )
        if attempt.status == "ready":
            return self._ready_response(attempt), status.HTTP_200_OK
        if attempt.status == "creating" and attempt.lease_owner == current_worker:
            return self._create_snap_transaction(attempt, current_worker)
        if attempt.status == "creating":
            return self._state_response(attempt), status.HTTP_202_ACCEPTED
        if attempt.status == "indeterminate":
            return self._reconcile(attempt, current_worker)
        if attempt.status == "resolved":
            return self._state_response(attempt), status.HTTP_200_OK
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Checkout attempt is not eligible for checkout",
        )

    def _create_snap_transaction(
        self, attempt: CheckoutAttempt, worker_id: str
    ) -> tuple[CheckoutResponse, int]:
        client = self.client_factory(self.settings)
        try:
            response = client.create_transaction(
                order_id=attempt.provider_order_id,
                gross_amount=attempt.amount,
            )
        except MidtransError as error:
            reason_code = _provider_error_code(error)
            logger.warning(
                "Midtrans checkout result is indeterminate",
                extra={
                    "payment_id": str(attempt.payment_id),
                    "checkout_attempt_id": str(attempt.id),
                    "provider_order_id": attempt.provider_order_id,
                    "reason_code": reason_code,
                },
            )
            self.foundation.mark_indeterminate(
                attempt.id,
                worker_id=worker_id,
                fencing_generation=attempt.fencing_generation,
                reason_code=reason_code,
            )
            self.session.refresh(attempt)
            return self._state_response(attempt), status.HTTP_202_ACCEPTED

        try:
            ciphertext = encrypt_checkout_response(
                self.settings,
                attempt.id,
                token=response.token,
                redirect_url=response.redirect_url,
            )
            context = CheckoutAttemptVerificationContext(
                attempt_id=attempt.id,
                provider_order_id=attempt.provider_order_id,
                fencing_generation=attempt.fencing_generation,
            )
            evidence = _issue_provider_evidence(
                context,
                CheckoutAttemptEvidenceState.READY,
                "snap_response_verified",
            )
            self.foundation.apply_verified_provider_evidence(
                attempt.id,
                worker_id=worker_id,
                fencing_generation=attempt.fencing_generation,
                evidence=evidence,
                provider_response_ciphertext=ciphertext,
            )
        except SQLAlchemyError:
            self.session.rollback()
            logger.error(
                "Midtrans checkout response persistence failed",
                extra={
                    "payment_id": str(attempt.payment_id),
                    "checkout_attempt_id": str(attempt.id),
                    "provider_order_id": attempt.provider_order_id,
                    "fencing_generation": attempt.fencing_generation,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Checkout response could not be persisted; reconciliation "
                    "is required"
                ),
                headers={"Retry-After": "30"},
            ) from None
        except ValueError:
            self.foundation.mark_indeterminate(
                attempt.id,
                worker_id=worker_id,
                fencing_generation=attempt.fencing_generation,
                reason_code="provider_response_unusable",
            )
            self.session.refresh(attempt)
            return self._state_response(attempt), status.HTTP_202_ACCEPTED

        self.session.refresh(attempt)
        return self._ready_response(attempt), status.HTTP_200_OK

    def _reconcile(
        self, attempt: CheckoutAttempt, worker_id: str
    ) -> tuple[CheckoutResponse, int]:
        try:
            leased_attempt = self.foundation.claim_reconciliation_lease(
                attempt.id,
                worker_id=worker_id,
            )
        except HTTPException as error:
            if error.status_code != status.HTTP_409_CONFLICT:
                raise
            current_attempt = self.repository.lock_checkout_attempt(attempt.id)
            if current_attempt is None:
                raise
            return self._state_response(current_attempt), status.HTTP_202_ACCEPTED

        payment = self.repository.get_payment(leased_attempt.payment_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout payment is inconsistent",
            )
        client = self.client_factory(self.settings)
        verifier = MidtransProviderVerifier(self.settings, client)
        try:
            result = verifier.reconcile_checkout_attempt(
                payment,
                leased_attempt,
                worker_id=worker_id,
                fencing_generation=leased_attempt.fencing_generation,
            )
        except MidtransNotFound:
            return self._defer_reconciliation(
                leased_attempt, worker_id, "status_not_found"
            )
        except MidtransError as error:
            return self._defer_reconciliation(
                leased_attempt, worker_id, _provider_error_code(error)
            )
        except ValueError:
            return self._defer_reconciliation(
                leased_attempt, worker_id, "status_identity_mismatch"
            )

        if result.manual_review_reason is not None:
            return self._defer_reconciliation(
                leased_attempt,
                worker_id,
                _safe_reason_code(result.manual_review_reason),
            )
        if result.outcome is None:
            return self._defer_reconciliation(
                leased_attempt, worker_id, "provider_pending_token_unavailable"
            )
        try:
            PaymentLifecycleService(self.session).record_verified_outcome(
                payment.id, result.outcome
            )
        except HTTPException as error:
            if error.status_code != status.HTTP_409_CONFLICT:
                raise
            self.session.rollback()
            current_attempt = self.repository.lock_checkout_attempt(attempt.id)
            if current_attempt is None:
                raise
            if current_attempt.lease_owner == worker_id:
                return self._defer_reconciliation(
                    current_attempt, worker_id, "payment_outcome_conflict"
                )
            return self._state_response(current_attempt), status.HTTP_202_ACCEPTED

        if result.outcome.status == "pending":
            return self._defer_reconciliation(
                leased_attempt, worker_id, "provider_pending_token_unavailable"
            )
        self.session.refresh(leased_attempt)
        return self._state_response(leased_attempt), status.HTTP_200_OK

    def _defer_reconciliation(
        self,
        attempt: CheckoutAttempt,
        worker_id: str,
        reason_code: str,
    ) -> tuple[CheckoutResponse, int]:
        if attempt.lease_owner == worker_id:
            self.foundation.release_reconciliation_lease(
                attempt.id,
                worker_id=worker_id,
                fencing_generation=attempt.fencing_generation,
                next_reconcile_at=datetime.now(timezone.utc) + _RECONCILE_DELAY,
                reason_code=_safe_reason_code(reason_code),
            )
        self.session.refresh(attempt)
        return self._state_response(attempt), status.HTTP_202_ACCEPTED

    def _ready_response(self, attempt: CheckoutAttempt) -> CheckoutResponse:
        ciphertext = attempt.provider_response_ciphertext
        if ciphertext is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Stored checkout response is unavailable",
            )
        try:
            checkout = decrypt_checkout_response(
                self.settings, attempt.id, ciphertext
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Stored checkout response is unavailable",
            ) from error
        return CheckoutResponse(
            payment_id=attempt.payment_id,
            attempt_id=attempt.id,
            provider_order_id=attempt.provider_order_id,
            status=attempt.status,
            token=checkout.token,
            redirect_url=checkout.redirect_url,
        )

    @staticmethod
    def _state_response(attempt: CheckoutAttempt) -> CheckoutResponse:
        return CheckoutResponse(
            payment_id=attempt.payment_id,
            attempt_id=attempt.id,
            provider_order_id=attempt.provider_order_id,
            status=attempt.status,
        )

    def _validate_configuration(self) -> None:
        try:
            self.settings.require_midtrans_server_key()
            self.settings.require_midtrans_token_encryption_key()
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Midtrans checkout is not configured",
            ) from error


def _provider_error_code(error: MidtransError) -> str:
    if isinstance(error, MidtransTimeout):
        return "provider_timeout"
    if isinstance(error, MidtransNetworkError):
        return "provider_network_error"
    if isinstance(error, MidtransHTTPError):
        return "provider_http_error"
    if isinstance(error, MidtransMalformedResponse):
        return "provider_malformed_response"
    if isinstance(error, MidtransNotFound):
        return "provider_not_found"
    return "provider_error"


def _safe_reason_code(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in value.lower()
    )
    return normalized[:100].strip("_") or "provider_review_required"
