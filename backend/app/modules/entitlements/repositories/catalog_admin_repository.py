from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription


class CatalogAdminRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_plans(
        self,
        *,
        search: str | None,
        billing_type: str | None,
        is_active: bool | None,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Plan], int]:
        statement = select(Plan)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            statement = statement.where(
                or_(Plan.name.ilike(term, escape="\\"), Plan.code.ilike(term, escape="\\"))
            )
        if billing_type is not None:
            statement = statement.where(Plan.billing_type == billing_type)
        if is_active is not None:
            statement = statement.where(Plan.is_active.is_(is_active))

        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        columns: dict[str, Any] = {
            "code": Plan.code,
            "name": Plan.name,
            "billing_type": Plan.billing_type,
            "price": Plan.price,
            "created_at": Plan.created_at,
            "updated_at": Plan.updated_at,
        }
        order_column = columns[sort_by]
        order_column = order_column.asc() if sort_order == "asc" else order_column.desc()
        statement = (
            statement.order_by(order_column, Plan.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement)), total

    def list_features(
        self,
        *,
        search: str | None,
        is_active: bool | None,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Feature], int]:
        statement = select(Feature)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            statement = statement.where(
                or_(
                    Feature.name.ilike(term, escape="\\"),
                    Feature.code.ilike(term, escape="\\"),
                )
            )
        if is_active is not None:
            statement = statement.where(Feature.is_active.is_(is_active))

        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        columns: dict[str, Any] = {
            "code": Feature.code,
            "name": Feature.name,
            "feature_type": Feature.feature_type,
            "created_at": Feature.created_at,
            "updated_at": Feature.updated_at,
        }
        order_column = columns[sort_by]
        order_column = order_column.asc() if sort_order == "asc" else order_column.desc()
        statement = (
            statement.order_by(order_column, Feature.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement)), total

    def get_plan(self, plan_id: UUID) -> Plan | None:
        return self.session.get(Plan, plan_id)

    def lock_plan(self, plan_id: UUID) -> Plan | None:
        return self.session.scalar(
            select(Plan).where(Plan.id == plan_id).with_for_update(of=Plan)
        )

    def get_feature(self, feature_id: UUID) -> Feature | None:
        return self.session.get(Feature, feature_id)

    def lock_feature(self, feature_id: UUID) -> Feature | None:
        return self.session.scalar(
            select(Feature).where(Feature.id == feature_id).with_for_update(of=Feature)
        )

    def lock_plans_for_feature(self, feature_id: UUID) -> None:
        plan_ids = list(
            self.session.scalars(
                select(PlanFeature.plan_id)
                .where(PlanFeature.feature_id == feature_id)
                .order_by(PlanFeature.plan_id)
            )
        )
        for plan_id in plan_ids:
            self.lock_plan(plan_id)

    def get_mapping(
        self, plan_id: UUID, feature_id: UUID
    ) -> PlanFeature | None:
        return self.session.scalar(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan_id,
                PlanFeature.feature_id == feature_id,
            )
        )

    def plan_mappings(
        self, plan_ids: list[UUID]
    ) -> dict[UUID, list[tuple[PlanFeature, Feature]]]:
        result: dict[UUID, list[tuple[PlanFeature, Feature]]] = {
            plan_id: [] for plan_id in plan_ids
        }
        if not plan_ids:
            return result
        rows = self.session.execute(
            select(PlanFeature, Feature)
            .join(Feature, Feature.id == PlanFeature.feature_id)
            .where(PlanFeature.plan_id.in_(plan_ids))
            .order_by(Feature.code, PlanFeature.id)
        )
        for mapping, feature in rows:
            result[mapping.plan_id].append((mapping, feature))
        return result

    def feature_mappings(
        self, feature_ids: list[UUID]
    ) -> dict[UUID, list[tuple[PlanFeature, Plan]]]:
        result: dict[UUID, list[tuple[PlanFeature, Plan]]] = {
            feature_id: [] for feature_id in feature_ids
        }
        if not feature_ids:
            return result
        rows = self.session.execute(
            select(PlanFeature, Plan)
            .join(Plan, Plan.id == PlanFeature.plan_id)
            .where(PlanFeature.feature_id.in_(feature_ids))
            .order_by(Plan.code, PlanFeature.id)
        )
        for mapping, plan in rows:
            result[mapping.feature_id].append((mapping, plan))
        return result

    def plan_has_legacy_transactions(self, plan_id: UUID) -> bool:
        now = datetime.now(timezone.utc)
        purchase = (
            select(PersonalPurchase.id)
            .where(
                PersonalPurchase.plan_id == plan_id,
                PersonalPurchase.entitlement_snapshot.is_(None),
                or_(
                    PersonalPurchase.status == "pending",
                    and_(
                        PersonalPurchase.status == "active",
                        or_(
                            PersonalPurchase.expires_at.is_(None),
                            PersonalPurchase.expires_at > now,
                        ),
                    ),
                ),
            )
            .exists()
        )
        subscription = (
            select(Subscription.id)
            .where(
                Subscription.plan_id == plan_id,
                Subscription.entitlement_snapshot.is_(None),
                or_(
                    Subscription.status == "pending",
                    and_(
                        Subscription.status.in_(("trialing", "active", "past_due")),
                        Subscription.current_period_end > now,
                    ),
                ),
            )
            .exists()
        )
        return bool(self.session.scalar(select(or_(purchase, subscription))))

    def feature_has_transactions(self, feature_id: UUID) -> bool:
        now = datetime.now(timezone.utc)
        feature_code = self.session.scalar(
            select(Feature.code).where(Feature.id == feature_id)
        )
        if feature_code is None:
            return False
        purchase = (
            select(PersonalPurchase.id)
            .join(PlanFeature, PlanFeature.plan_id == PersonalPurchase.plan_id)
            .where(
                PlanFeature.feature_id == feature_id,
                PersonalPurchase.entitlement_snapshot.is_(None),
                or_(
                    PersonalPurchase.status == "pending",
                    and_(
                        PersonalPurchase.status == "active",
                        or_(
                            PersonalPurchase.expires_at.is_(None),
                            PersonalPurchase.expires_at > now,
                        ),
                    ),
                ),
            )
            .exists()
        )
        subscription = (
            select(Subscription.id)
            .join(PlanFeature, PlanFeature.plan_id == Subscription.plan_id)
            .where(
                PlanFeature.feature_id == feature_id,
                Subscription.entitlement_snapshot.is_(None),
                or_(
                    Subscription.status == "pending",
                    and_(
                        Subscription.status.in_(("trialing", "active", "past_due")),
                        Subscription.current_period_end > now,
                    ),
                ),
            )
            .exists()
        )
        if self.session.scalar(select(or_(purchase, subscription))):
            return True

        purchase_snapshots = self.session.scalars(
            select(PersonalPurchase.entitlement_snapshot).where(
                PersonalPurchase.entitlement_snapshot.is_not(None),
                or_(
                    PersonalPurchase.status == "pending",
                    and_(
                        PersonalPurchase.status == "active",
                        or_(
                            PersonalPurchase.expires_at.is_(None),
                            PersonalPurchase.expires_at > now,
                        ),
                    ),
                ),
            )
        )
        subscription_snapshots = self.session.scalars(
            select(Subscription.entitlement_snapshot).where(
                Subscription.entitlement_snapshot.is_not(None),
                or_(
                    Subscription.status == "pending",
                    and_(
                        Subscription.status.in_(("trialing", "active", "past_due")),
                        or_(
                            Subscription.current_period_end.is_(None),
                            Subscription.current_period_end > now,
                        ),
                    ),
                ),
            )
        )
        return any(
            self._snapshot_contains_code(snapshot, feature_code)
            for snapshot in (*purchase_snapshots, *subscription_snapshots)
        )

    @staticmethod
    def _snapshot_contains_code(snapshot: Any, feature_code: str) -> bool:
        features = snapshot.get("features") if isinstance(snapshot, dict) else None
        return isinstance(features, dict) and feature_code in features

    def feature_has_overrides(self, feature_id: UUID) -> bool:
        now = datetime.now(timezone.utc)
        return (
            self.session.scalar(
                select(EntitlementOverride.id)
                .where(
                    EntitlementOverride.feature_id == feature_id,
                    EntitlementOverride.is_enabled.is_not(None),
                    or_(
                        EntitlementOverride.expires_at.is_(None),
                        EntitlementOverride.expires_at > now,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    def add(self, entity: Plan | Feature | PlanFeature) -> None:
        self.session.add(entity)

    def delete_mapping(self, mapping: PlanFeature) -> None:
        self.session.delete(mapping)
