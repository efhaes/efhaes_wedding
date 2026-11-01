from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.database.base import JSONValue
from app.modules.invitations.models.invitation import Invitation


class EntitlementRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_feature(self, feature_code: str) -> Feature | None:
        return self.session.scalar(select(Feature).where(Feature.code == feature_code))

    def overrides_for_invitation(
        self,
        invitation_id: UUID,
        feature_code: str,
        now: datetime,
    ) -> list[EntitlementOverride]:
        return list(
            self.session.scalars(
                select(EntitlementOverride)
                .join(Feature, Feature.id == EntitlementOverride.feature_id)
                .where(
                    EntitlementOverride.invitation_id == invitation_id,
                    Feature.code == feature_code,
                    or_(
                        EntitlementOverride.expires_at.is_(None),
                        EntitlementOverride.expires_at > now,
                    ),
                )
            )
        )

    def overrides_for_workspace(
        self,
        workspace_id: UUID,
        feature_code: str,
        now: datetime,
    ) -> list[EntitlementOverride]:
        return list(
            self.session.scalars(
                select(EntitlementOverride)
                .join(Feature, Feature.id == EntitlementOverride.feature_id)
                .where(
                    EntitlementOverride.workspace_id == workspace_id,
                    Feature.code == feature_code,
                    or_(
                        EntitlementOverride.expires_at.is_(None),
                        EntitlementOverride.expires_at > now,
                    ),
                )
            )
        )

    def has_personal_invitation_access(
        self,
        user_id: UUID,
        invitation_id: UUID,
    ) -> bool:
        return (
            self.session.scalar(
                select(Invitation.id).where(
                    Invitation.id == invitation_id,
                    Invitation.owner_id == user_id,
                    Invitation.workspace_id.is_(None),
                )
            )
            is not None
        )

    def lock_unassigned_personal_purchase(
        self,
        user_id: UUID,
        feature_code: str,
        now: datetime,
    ) -> PersonalPurchase | None:
        has_payment = (
            select(Payment.id)
            .where(Payment.personal_purchase_id == PersonalPurchase.id)
            .exists()
        )
        has_settled_payment = (
            select(Payment.id)
            .where(
                Payment.personal_purchase_id == PersonalPurchase.id,
                Payment.status == "paid",
                Payment.paid_at.is_not(None),
                Payment.paid_at <= now,
            )
            .exists()
        )
        statement = (
            select(PersonalPurchase)
            .join(Plan, Plan.id == PersonalPurchase.plan_id)
            .where(
                PersonalPurchase.user_id == user_id,
                PersonalPurchase.invitation_id.is_(None),
                PersonalPurchase.status == "active",
                PersonalPurchase.activated_at.is_not(None),
                PersonalPurchase.activated_at <= now,
                or_(
                    PersonalPurchase.expires_at.is_(None),
                    PersonalPurchase.expires_at > now,
                ),
                Plan.billing_type == "one_time",
                or_(~has_payment, has_settled_payment),
            )
            .order_by(
                PersonalPurchase.expires_at.asc().nulls_last(),
                PersonalPurchase.activated_at.asc(),
                PersonalPurchase.id,
            )
            .with_for_update(of=PersonalPurchase)
        )
        purchases = self.session.scalars(statement)
        for purchase in purchases:
            if purchase.entitlement_snapshot is not None:
                if self._snapshot_configuration(
                    purchase.entitlement_snapshot, feature_code
                ) is not None:
                    return purchase
                continue
            mapping = self.session.scalar(
                select(PlanFeature)
                .join(Feature, Feature.id == PlanFeature.feature_id)
                .where(
                    PlanFeature.plan_id == purchase.plan_id,
                    PlanFeature.is_enabled.is_(True),
                    Feature.code == feature_code,
                    Feature.is_active.is_(True),
                )
            )
            if mapping is not None:
                return purchase
        return None

    def has_active_workspace_membership(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> bool:
        return (
            self.session.scalar(
                select(WorkspaceMember.id)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                    WorkspaceMember.status == "active",
                    Workspace.status == "active",
                )
            )
            is not None
        )

    def personal_plan_features(
        self,
        user_id: UUID,
        invitation_id: UUID,
        feature_code: str,
        now: datetime,
    ) -> list[tuple[Plan, JSONValue, PersonalPurchase]]:
        has_payment = (
            select(Payment.id)
            .where(Payment.personal_purchase_id == PersonalPurchase.id)
            .exists()
        )
        has_settled_payment = (
            select(Payment.id)
            .where(
                Payment.personal_purchase_id == PersonalPurchase.id,
                Payment.status == "paid",
                Payment.paid_at.is_not(None),
                Payment.paid_at <= now,
            )
            .exists()
        )
        conditions = (
            PersonalPurchase.user_id == user_id,
            PersonalPurchase.invitation_id == invitation_id,
            PersonalPurchase.status == "active",
            PersonalPurchase.activated_at.is_not(None),
            PersonalPurchase.activated_at <= now,
            or_(
                PersonalPurchase.expires_at.is_(None),
                PersonalPurchase.expires_at > now,
            ),
            Plan.billing_type == "one_time",
            or_(~has_payment, has_settled_payment),
        )
        results: list[tuple[Plan, JSONValue, PersonalPurchase]] = []
        snapshots = self.session.execute(
            select(Plan, PersonalPurchase)
            .join(Plan, Plan.id == PersonalPurchase.plan_id)
            .where(
                *conditions,
                PersonalPurchase.entitlement_snapshot.is_not(None),
            )
            .order_by(
                PersonalPurchase.expires_at.desc().nulls_last(),
                PersonalPurchase.activated_at.desc(),
            )
        )
        for plan, purchase in snapshots:
            configuration = self._snapshot_configuration(
                purchase.entitlement_snapshot, feature_code
            )
            if configuration is not None:
                results.append((plan, configuration, purchase))

        legacy = self.session.execute(
            select(Plan, PlanFeature.configuration, PersonalPurchase)
            .select_from(PersonalPurchase)
            .join(Plan, Plan.id == PersonalPurchase.plan_id)
            .join(PlanFeature, PlanFeature.plan_id == Plan.id)
            .join(Feature, Feature.id == PlanFeature.feature_id)
            .where(
                *conditions,
                PersonalPurchase.entitlement_snapshot.is_(None),
                Feature.code == feature_code,
                Feature.is_active.is_(True),
                PlanFeature.is_enabled.is_(True),
            )
            .order_by(
                PersonalPurchase.expires_at.desc().nulls_last(),
                PersonalPurchase.activated_at.desc(),
            )
        )
        results.extend(legacy)
        return results

    def workspace_plan_features(
        self,
        workspace_id: UUID,
        feature_code: str,
        now: datetime,
    ) -> list[tuple[Plan, JSONValue, datetime | None]]:
        conditions = (
            Subscription.workspace_id == workspace_id,
            Subscription.status.in_(("active", "trialing")),
            Subscription.current_period_start.is_not(None),
            Subscription.current_period_start <= now,
            Subscription.current_period_end.is_not(None),
            Subscription.current_period_end > now,
            Plan.billing_type == "subscription",
        )
        results: list[tuple[Plan, JSONValue, datetime | None]] = []
        snapshots = self.session.execute(
            select(Plan, Subscription)
            .select_from(Subscription)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                *conditions,
                Subscription.entitlement_snapshot.is_not(None),
            )
            .order_by(Subscription.current_period_end.desc())
        )
        for plan, subscription in snapshots:
            configuration = self._snapshot_configuration(
                subscription.entitlement_snapshot, feature_code
            )
            if configuration is not None:
                results.append(
                    (plan, configuration, subscription.current_period_end)
                )

        legacy = self.session.execute(
            select(Plan, PlanFeature.configuration, Subscription.current_period_end)
            .select_from(Subscription)
            .join(Plan, Plan.id == Subscription.plan_id)
            .join(PlanFeature, PlanFeature.plan_id == Plan.id)
            .join(Feature, Feature.id == PlanFeature.feature_id)
            .where(
                *conditions,
                Subscription.entitlement_snapshot.is_(None),
                Feature.code == feature_code,
                Feature.is_active.is_(True),
                PlanFeature.is_enabled.is_(True),
            )
            .order_by(Subscription.current_period_end.desc())
        )
        results.extend(legacy)
        return results

    @staticmethod
    def _snapshot_configuration(
        snapshot: JSONValue | None,
        feature_code: str,
    ) -> JSONValue | None:
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            return None
        features = snapshot.get("features")
        if not isinstance(features, dict):
            return None
        feature = features.get(feature_code)
        if not isinstance(feature, dict):
            return None
        configuration = feature.get("configuration")
        return configuration if isinstance(configuration, dict) else None
