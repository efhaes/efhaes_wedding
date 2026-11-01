from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription


PREMIUM_TEMPLATE_FEATURE_CODE = "premium_templates"


class EntitlementRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def overrides_for_invitation(
        self,
        invitation_id: UUID,
    ) -> list[EntitlementOverride]:
        return list(
            self.session.scalars(
                select(EntitlementOverride)
                .join(Feature, Feature.id == EntitlementOverride.feature_id)
                .where(
                    EntitlementOverride.invitation_id == invitation_id,
                    Feature.code == PREMIUM_TEMPLATE_FEATURE_CODE,
                )
            )
        )

    def overrides_for_workspace(
        self,
        workspace_id: UUID,
    ) -> list[EntitlementOverride]:
        return list(
            self.session.scalars(
                select(EntitlementOverride)
                .join(Feature, Feature.id == EntitlementOverride.feature_id)
                .where(
                    EntitlementOverride.workspace_id == workspace_id,
                    Feature.code == PREMIUM_TEMPLATE_FEATURE_CODE,
                )
            )
        )

    def has_active_personal_purchase(
        self,
        user_id: UUID,
        invitation_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            select(PersonalPurchase.id)
            .join(Plan, Plan.id == PersonalPurchase.plan_id)
            .join(PlanFeature, PlanFeature.plan_id == Plan.id)
            .join(Feature, Feature.id == PlanFeature.feature_id)
            .where(
                PersonalPurchase.user_id == user_id,
                PersonalPurchase.invitation_id == invitation_id,
                PersonalPurchase.status == "active",
                PersonalPurchase.activated_at.is_not(None),
                PersonalPurchase.activated_at <= now,
                or_(
                    PersonalPurchase.expires_at.is_(None),
                    PersonalPurchase.expires_at > now,
                ),
                Plan.is_active.is_(True),
                Plan.billing_type == "one_time",
                Feature.code == PREMIUM_TEMPLATE_FEATURE_CODE,
            )
            .limit(1)
        )
        return self.session.scalar(statement) is not None

    def has_active_workspace_subscription(
        self,
        workspace_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            select(Subscription.id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .join(PlanFeature, PlanFeature.plan_id == Plan.id)
            .join(Feature, Feature.id == PlanFeature.feature_id)
            .where(
                Subscription.workspace_id == workspace_id,
                Subscription.status.in_(("active", "trialing")),
                Subscription.current_period_start <= now,
                Subscription.current_period_end > now,
                Plan.is_active.is_(True),
                Plan.billing_type == "subscription",
                Feature.code == PREMIUM_TEMPLATE_FEATURE_CODE,
            )
            .limit(1)
        )
        return self.session.scalar(statement) is not None
