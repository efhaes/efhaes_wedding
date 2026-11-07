from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feature import Feature
from app.models.checkout_attempt import CheckoutAttempt
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember


class BillingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_plan(self, plan_id: UUID) -> Plan | None:
        return self.session.scalar(
            select(Plan).where(Plan.id == plan_id).with_for_update(of=Plan)
        )

    def lock_workspace(self, workspace_id: UUID) -> Workspace | None:
        return self.session.scalar(
            select(Workspace)
            .where(Workspace.id == workspace_id)
            .with_for_update(of=Workspace)
        )

    def active_billing_membership(
        self, workspace_id: UUID, user_id: UUID
    ) -> WorkspaceMember | None:
        return self.session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
                WorkspaceMember.role.in_(("owner", "admin")),
            )
            .with_for_update(of=WorkspaceMember)
        )

    def snapshot_plan_features(self, plan_id: UUID) -> dict:
        rows = self.session.execute(
            select(Feature.code, Feature.feature_type, PlanFeature.configuration)
            .join(PlanFeature, PlanFeature.feature_id == Feature.id)
            .where(
                PlanFeature.plan_id == plan_id,
                PlanFeature.is_enabled.is_(True),
                Feature.is_active.is_(True),
            )
            .order_by(Feature.code)
        )
        return {
            "version": 1,
            "features": {
                code: {
                    "feature_type": feature_type,
                    "configuration": configuration,
                }
                for code, feature_type, configuration in rows
            },
        }

    def lock_purchase(self, purchase_id: UUID) -> PersonalPurchase | None:
        return self.session.scalar(
            select(PersonalPurchase)
            .where(PersonalPurchase.id == purchase_id)
            .with_for_update(of=PersonalPurchase)
        )

    def lock_subscription(self, subscription_id: UUID) -> Subscription | None:
        return self.session.scalar(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update(of=Subscription)
        )

    def subscription_workspace_id(self, subscription_id: UUID) -> UUID | None:
        return self.session.scalar(
            select(Subscription.workspace_id).where(
                Subscription.id == subscription_id
            )
        )

    def lock_payment(self, payment_id: UUID) -> Payment | None:
        return self.session.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update(of=Payment)
        )

    def checkout_attempt_for_payment(
        self, payment_id: UUID, *, lock: bool = False
    ) -> CheckoutAttempt | None:
        statement = select(CheckoutAttempt).where(
            CheckoutAttempt.payment_id == payment_id
        )
        if lock:
            statement = statement.with_for_update(of=CheckoutAttempt)
        return self.session.scalar(statement)

    def lock_checkout_attempt(self, attempt_id: UUID) -> CheckoutAttempt | None:
        return self.session.scalar(
            select(CheckoutAttempt)
            .where(CheckoutAttempt.id == attempt_id)
            .with_for_update(of=CheckoutAttempt)
        )

    def due_checkout_attempt_ids(
        self, now: datetime, *, limit: int
    ) -> list[UUID]:
        statement = (
            select(CheckoutAttempt.id)
            .where(
                (
                    (CheckoutAttempt.status.in_(("ready", "indeterminate")))
                    & (
                        CheckoutAttempt.lease_until.is_(None)
                        | (CheckoutAttempt.lease_until <= now)
                    )
                    & (
                        CheckoutAttempt.next_reconcile_at.is_(None)
                        | (CheckoutAttempt.next_reconcile_at <= now)
                    )
                )
                | (
                    (CheckoutAttempt.status == "creating")
                    & (
                        CheckoutAttempt.lease_until.is_(None)
                        | (CheckoutAttempt.lease_until <= now)
                    )
                )
            )
            .order_by(CheckoutAttempt.next_reconcile_at, CheckoutAttempt.created_at)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def active_or_pending_subscriptions(
        self, workspace_id: UUID
    ) -> list[Subscription]:
        statement = (
            select(Subscription)
            .where(
                Subscription.workspace_id == workspace_id,
                Subscription.status.in_(
                    ("pending", "trialing", "active", "past_due")
                ),
            )
            .order_by(Subscription.created_at, Subscription.id)
            .with_for_update(of=Subscription)
        )
        return list(self.session.scalars(statement))

    def has_other_paid_payment(
        self,
        payment: Payment,
    ) -> bool:
        statement = select(Payment.id).where(
            Payment.id != payment.id,
            Payment.status == "paid",
        )
        if payment.personal_purchase_id is not None:
            statement = statement.where(
                Payment.personal_purchase_id == payment.personal_purchase_id
            )
        else:
            statement = statement.where(
                Payment.subscription_id == payment.subscription_id
            )
        return self.session.scalar(statement.limit(1)) is not None

    def get_personal_purchase(self, purchase_id: UUID) -> PersonalPurchase | None:
        return self.session.get(PersonalPurchase, purchase_id)

    def get_subscription(self, subscription_id: UUID) -> Subscription | None:
        return self.session.get(Subscription, subscription_id)

    def get_payment(self, payment_id: UUID) -> Payment | None:
        return self.session.get(Payment, payment_id)

    def payment_for_provider_order_id(
        self, provider: str, provider_order_id: str, *, lock: bool = False
    ) -> Payment | None:
        statement = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_order_id == provider_order_id,
        )
        if lock:
            statement = statement.with_for_update(of=Payment)
        return self.session.scalar(statement)

    def payment_for_purchase(self, purchase_id: UUID) -> Payment | None:
        return self.session.scalar(
            select(Payment)
            .where(Payment.personal_purchase_id == purchase_id)
            .order_by(Payment.created_at, Payment.id)
        )

    def payment_for_subscription(self, subscription_id: UUID) -> Payment | None:
        return self.session.scalar(
            select(Payment)
            .where(Payment.subscription_id == subscription_id)
            .order_by(Payment.created_at, Payment.id)
        )

    def add(self, *entities: object) -> None:
        self.session.add_all(entities)
