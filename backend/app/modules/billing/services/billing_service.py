from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.billing_idempotency_record import BillingIdempotencyRecord
from app.models.personal_purchase import PersonalPurchase
from app.models.subscription import Subscription
from app.models.workspace import WorkspaceMember
from app.modules.billing.repositories.billing_repository import BillingRepository
from app.modules.billing.schemas.transactions import (
    PaymentRead,
    PersonalPurchaseRead,
    WorkspaceSubscriptionRead,
)
from app.modules.billing.services.idempotency import (
    claim_idempotency_record,
    complete_idempotency_record,
    hash_idempotency_key,
    mark_idempotency_replayed,
    request_fingerprint,
)
from app.modules.users.models.user import User


class BillingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = BillingRepository(session)

    def create_personal_purchase(
        self, user: User, plan_id: UUID, idempotency_key: str
    ) -> PersonalPurchaseRead:
        key_hash = hash_idempotency_key(idempotency_key)
        fingerprint = request_fingerprint(
            "personal_purchase", {"plan_id": str(plan_id)}
        )
        idempotency_record, claimed = claim_idempotency_record(
            self.session,
            user_id=user.id,
            operation="personal_purchase",
            key_hash=key_hash,
            fingerprint=fingerprint,
        )
        if not claimed:
            return self._replay_personal_purchase(idempotency_record)

        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        if not plan.is_active or plan.billing_type != "one_time":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plan is not available for personal purchases",
            )
        if (
            plan.price is None
            or plan.price <= 0
            or not self._valid_currency(plan.currency)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plan is not configured for paid personal purchases",
            )
        snapshot = self.repository.snapshot_plan_features(plan.id)
        try:
            purchase = PersonalPurchase.pending_from_plan(
                user.id, plan, entitlement_snapshot=snapshot
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plan is not configured for personal purchases",
            ) from error

        self.repository.add(purchase)
        self._flush()
        payment = Payment(
            personal_purchase_id=purchase.id,
            provider=None,
            amount=plan.price,
            currency=plan.currency,
            status="pending",
        )
        self.repository.add(payment)
        self._flush()
        complete_idempotency_record(
            idempotency_record,
            personal_purchase_id=purchase.id,
            payment_id=payment.id,
        )
        self._commit()
        return self._purchase_read(purchase, payment)

    def get_personal_purchase(
        self, user: User, purchase_id: UUID
    ) -> PersonalPurchaseRead:
        purchase = self.repository.get_personal_purchase(purchase_id)
        if purchase is None or purchase.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal purchase not found",
            )
        return self._purchase_read(
            purchase, self.repository.payment_for_purchase(purchase.id)
        )

    def create_workspace_subscription(
        self,
        user: User,
        workspace_id: UUID,
        plan_id: UUID,
        idempotency_key: str,
    ) -> WorkspaceSubscriptionRead:
        key_hash = hash_idempotency_key(idempotency_key)
        fingerprint = request_fingerprint(
            "workspace_subscription",
            {"plan_id": str(plan_id), "workspace_id": str(workspace_id)},
        )
        idempotency_record, claimed = claim_idempotency_record(
            self.session,
            user_id=user.id,
            operation="workspace_subscription",
            key_hash=key_hash,
            fingerprint=fingerprint,
        )
        if not claimed:
            return self._replay_workspace_subscription(idempotency_record, user)

        workspace = self.repository.lock_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
            )
        if workspace.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace is not active",
            )
        membership = self.repository.active_billing_membership(
            workspace.id, user.id
        )
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace owner or admin membership is required",
            )

        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        if (
            not plan.is_active
            or plan.billing_type != "subscription"
            or plan.duration_type != "recurring"
            or plan.duration_days is not None
            or plan.billing_period_days is None
            or plan.billing_period_days <= 0
            or plan.price is None
            or plan.price <= Decimal("0")
            or not self._valid_currency(plan.currency)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plan is not configured for workspace subscriptions",
            )

        now = datetime.now(timezone.utc)
        expired_subscriptions: list[Subscription] = []
        for existing in self.repository.active_or_pending_subscriptions(workspace.id):
            if existing.status == "pending":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A pending workspace subscription already exists",
                )
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

        snapshot = self.repository.snapshot_plan_features(plan.id)
        subscription = Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status="pending",
            current_period_start=None,
            current_period_end=None,
            billing_period_days_snapshot=plan.billing_period_days,
            purchased_price=plan.price,
            purchased_currency=plan.currency,
            entitlement_snapshot=snapshot,
        )
        self.repository.add(subscription)
        self._flush()
        payment = Payment(
            subscription_id=subscription.id,
            provider=None,
            amount=plan.price,
            currency=plan.currency,
            status="pending",
        )
        self.repository.add(payment)
        self._flush()
        complete_idempotency_record(
            idempotency_record,
            subscription_id=subscription.id,
            payment_id=payment.id,
        )
        self._commit()
        return self._subscription_read(subscription, payment)

    def get_workspace_subscription(
        self, user: User, subscription_id: UUID
    ) -> WorkspaceSubscriptionRead:
        subscription = self.repository.get_subscription(subscription_id)
        if subscription is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace subscription not found",
            )
        self._require_workspace_billing_role(subscription.workspace_id, user.id)
        return self._subscription_read(
            subscription, self.repository.payment_for_subscription(subscription.id)
        )

    def get_payment(self, user: User, payment_id: UUID) -> PaymentRead:
        payment = self.repository.get_payment(payment_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
            )
        if payment.personal_purchase_id is not None:
            purchase = self.repository.get_personal_purchase(
                payment.personal_purchase_id
            )
            if purchase is None or purchase.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
                )
        elif payment.subscription_id is not None:
            subscription = self.repository.get_subscription(payment.subscription_id)
            if subscription is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
                )
            self._require_workspace_billing_role(subscription.workspace_id, user.id)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment target is inconsistent",
            )
        return self._payment_read(payment)

    def _require_workspace_billing_role(
        self, workspace_id: UUID, user_id: UUID
    ) -> WorkspaceMember:
        membership = self.repository.active_billing_membership(workspace_id, user_id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace subscription not found",
            )
        return membership

    def _replay_personal_purchase(
        self, record: BillingIdempotencyRecord
    ) -> PersonalPurchaseRead:
        if record.personal_purchase_id is None or record.payment_id is None:
            raise RuntimeError("Completed purchase idempotency record is incomplete")
        purchase = self.repository.get_personal_purchase(record.personal_purchase_id)
        payment = self.repository.get_payment(record.payment_id)
        if purchase is None or payment is None:
            raise RuntimeError("Completed purchase idempotency result is missing")
        if payment.personal_purchase_id != purchase.id:
            raise RuntimeError("Purchase idempotency result has inconsistent payment")
        mark_idempotency_replayed(record)
        self._commit()
        return self._purchase_read(purchase, payment)

    def _replay_workspace_subscription(
        self, record: BillingIdempotencyRecord, user: User
    ) -> WorkspaceSubscriptionRead:
        if record.subscription_id is None or record.payment_id is None:
            raise RuntimeError(
                "Completed subscription idempotency record is incomplete"
            )
        subscription = self.repository.get_subscription(record.subscription_id)
        payment = self.repository.get_payment(record.payment_id)
        if subscription is None or payment is None:
            raise RuntimeError("Completed subscription idempotency result is missing")
        if payment.subscription_id != subscription.id:
            raise RuntimeError(
                "Subscription idempotency result has inconsistent payment"
            )
        self._require_workspace_billing_role(subscription.workspace_id, user.id)
        mark_idempotency_replayed(record)
        self._commit()
        return self._subscription_read(subscription, payment)

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction conflicts with existing data",
            ) from error

    def _flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction conflicts with existing data",
            ) from error

    @staticmethod
    def _valid_currency(currency: str | None) -> bool:
        return (
            currency is not None
            and len(currency) == 3
            and all("A" <= character <= "Z" for character in currency)
        )

    @staticmethod
    def _payment_read(payment: Payment | None) -> PaymentRead | None:
        if payment is None:
            return None
        return PaymentRead(
            id=payment.id,
            personal_purchase_id=payment.personal_purchase_id,
            subscription_id=payment.subscription_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            paid_at=payment.paid_at,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
        )

    @classmethod
    def _purchase_read(
        cls, purchase: PersonalPurchase, payment: Payment | None
    ) -> PersonalPurchaseRead:
        return PersonalPurchaseRead(
            id=purchase.id,
            plan_id=purchase.plan_id,
            status=purchase.status,
            purchased_price=purchase.purchased_price,
            purchased_currency=purchase.purchased_currency,
            duration_type_snapshot=purchase.duration_type_snapshot,
            duration_days_snapshot=purchase.duration_days_snapshot,
            activated_at=purchase.activated_at,
            expires_at=purchase.expires_at,
            created_at=purchase.created_at,
            updated_at=purchase.updated_at,
            payment=cls._payment_read(payment),
        )

    @classmethod
    def _subscription_read(
        cls, subscription: Subscription, payment: Payment | None
    ) -> WorkspaceSubscriptionRead:
        return WorkspaceSubscriptionRead(
            id=subscription.id,
            workspace_id=subscription.workspace_id,
            plan_id=subscription.plan_id,
            status=subscription.status,
            purchased_price=subscription.purchased_price,
            purchased_currency=subscription.purchased_currency,
            billing_period_days_snapshot=subscription.billing_period_days_snapshot,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            created_at=subscription.created_at,
            updated_at=subscription.updated_at,
            payment=cls._payment_read(payment),
        )
