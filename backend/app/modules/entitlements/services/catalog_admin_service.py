from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.feature import Feature
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.modules.entitlements.repositories.catalog_admin_repository import (
    CatalogAdminRepository,
)
from app.modules.entitlements.schemas.admin_catalog import (
    FeatureCreate,
    FeaturePage,
    FeaturePlanMappingRead,
    FeatureRead,
    FeatureSummary,
    FeatureUpdate,
    PlanCreate,
    PlanFeatureCreate,
    PlanFeatureRead,
    PlanFeatureUpdate,
    PlanPage,
    PlanRead,
    PlanSummary,
    PlanUpdate,
)
from app.modules.entitlements.schemas.catalog import PlanConfiguration


class CatalogAdminService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = CatalogAdminRepository(session)

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
    ) -> PlanPage:
        plans, total = self.repository.list_plans(
            search=search.strip() if search else None,
            billing_type=billing_type,
            is_active=is_active,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        mappings = self.repository.plan_mappings([plan.id for plan in plans])
        return PlanPage(
            items=[
                self._plan_read(plan, mappings.get(plan.id, [])) for plan in plans
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def get_plan(self, plan_id: UUID) -> PlanRead:
        plan = self._get_plan(plan_id)
        mappings = self.repository.plan_mappings([plan.id]).get(plan.id, [])
        return self._plan_read(plan, mappings)

    def create_plan(self, payload: PlanCreate) -> PlanRead:
        data = payload.model_dump()
        data["name"] = data["name"].strip()
        if data["description"] is not None:
            data["description"] = data["description"].strip() or None
        configuration = self._validate_plan_configuration(data)
        plan = Plan(**configuration.model_dump())
        self.repository.add(plan)
        self._commit()
        return self._plan_read(plan, [])

    def update_plan(self, plan_id: UUID, payload: PlanUpdate) -> PlanRead:
        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] is not None:
            changes["name"] = changes["name"].strip()
            if not changes["name"]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Plan name must not be blank",
                )
        if "description" in changes and changes["description"] is not None:
            changes["description"] = changes["description"].strip() or None
        configuration = {
            "code": plan.code,
            "name": plan.name,
            "description": plan.description,
            "billing_type": plan.billing_type,
            "duration_type": plan.duration_type,
            "duration_days": plan.duration_days,
            "billing_period_days": plan.billing_period_days,
            "price": plan.price,
            "currency": plan.currency,
            "is_active": plan.is_active,
        }
        configuration.update(changes)
        validated = self._validate_plan_configuration(configuration)
        for field, value in validated.model_dump().items():
            setattr(plan, field, value)
        self._commit()
        mappings = self.repository.plan_mappings([plan.id]).get(plan.id, [])
        return self._plan_read(plan, mappings)

    def list_features(
        self,
        *,
        search: str | None,
        is_active: bool | None,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> FeaturePage:
        features, total = self.repository.list_features(
            search=search.strip() if search else None,
            is_active=is_active,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        mappings = self.repository.feature_mappings([feature.id for feature in features])
        return FeaturePage(
            items=[
                self._feature_read(feature, mappings.get(feature.id, []))
                for feature in features
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def get_feature(self, feature_id: UUID) -> FeatureRead:
        feature = self._get_feature(feature_id)
        mappings = self.repository.feature_mappings([feature.id]).get(feature.id, [])
        return self._feature_read(feature, mappings)

    def create_feature(self, payload: FeatureCreate) -> FeatureRead:
        data = payload.model_dump()
        data["name"] = data["name"].strip()
        if not data["name"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Feature name must not be blank",
            )
        if data["description"] is not None:
            data["description"] = data["description"].strip() or None
        feature = Feature(**data)
        self.repository.add(feature)
        self._commit()
        return self._feature_read(feature, [])

    def update_feature(
        self, feature_id: UUID, payload: FeatureUpdate
    ) -> FeatureRead:
        feature = self._get_feature(feature_id)
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Feature name must not be null",
            )
        if "description" in changes and changes["description"] is not None:
            changes["description"] = changes["description"].strip() or None
        if "is_active" in changes:
            self.repository.lock_plans_for_feature(feature.id)
            feature = self.repository.lock_feature(feature_id)
            if feature is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Feature not found"
                )
        if (
            changes.get("is_active") is False
            and feature.is_active
            and (
                self.repository.feature_has_transactions(feature.id)
                or self.repository.feature_has_overrides(feature.id)
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Feature cannot be deactivated while existing transactions "
                    "or overrides depend on it"
                )
            )
        for field, value in changes.items():
            if field == "name" and value is not None:
                value = value.strip()
                if not value:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Feature name must not be blank",
                    )
            setattr(feature, field, value)
        self._commit()
        mappings = self.repository.feature_mappings([feature.id]).get(feature.id, [])
        return self._feature_read(feature, mappings)

    def list_plan_features(self, plan_id: UUID) -> list[PlanFeatureRead]:
        self._get_plan(plan_id)
        mappings = self.repository.plan_mappings([plan_id]).get(plan_id, [])
        return [self._mapping_read(mapping, feature) for mapping, feature in mappings]

    def add_plan_feature(
        self, plan_id: UUID, payload: PlanFeatureCreate
    ) -> PlanFeatureRead:
        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        feature = self.repository.lock_feature(payload.feature_id)
        if feature is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Feature not found"
            )
        self._ensure_plan_mappings_mutable(plan.id)
        self._validate_mapping_configuration(
            feature, payload.is_enabled, payload.configuration
        )
        mapping = PlanFeature(
            plan_id=plan.id,
            feature_id=feature.id,
            is_enabled=payload.is_enabled,
            configuration=payload.configuration,
        )
        self.repository.add(mapping)
        self._commit()
        return self._mapping_read(mapping, feature)

    def update_plan_feature(
        self,
        plan_id: UUID,
        feature_id: UUID,
        payload: PlanFeatureUpdate,
    ) -> PlanFeatureRead:
        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        mapping = self.repository.get_mapping(plan_id, feature_id)
        if mapping is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan feature mapping not found",
            )
        feature = self.repository.lock_feature(feature_id)
        if feature is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Feature not found"
            )
        self._ensure_plan_mappings_mutable(plan.id)
        changes = payload.model_dump(exclude_unset=True)
        if "configuration" in changes and changes["configuration"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Configuration must be a JSON object",
            )
        enabled = changes.get("is_enabled", mapping.is_enabled)
        configuration = changes.get("configuration", mapping.configuration)
        self._validate_mapping_configuration(feature, enabled, configuration)
        mapping.is_enabled = enabled
        mapping.configuration = configuration
        self._commit()
        return self._mapping_read(mapping, feature)

    def delete_plan_feature(self, plan_id: UUID, feature_id: UUID) -> None:
        plan = self.repository.lock_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        mapping = self.repository.get_mapping(plan_id, feature_id)
        if mapping is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan feature mapping not found",
            )
        self._ensure_plan_mappings_mutable(plan.id)
        self.repository.delete_mapping(mapping)
        self._commit()

    def _ensure_plan_mappings_mutable(self, plan_id: UUID) -> None:
        if self.repository.plan_has_legacy_transactions(plan_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Plan feature mappings cannot change while legacy transactions "
                    "without entitlement snapshots are active"
                ),
            )

    def _validate_plan_configuration(self, data: dict[str, Any]) -> PlanConfiguration:
        try:
            configuration = PlanConfiguration.model_validate(data)
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Plan configuration is invalid",
            ) from error
        if configuration.is_active and (
            configuration.price is None
            or configuration.duration_type == "pending"
            or (
                configuration.billing_type == "subscription"
                and configuration.billing_period_days is None
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "An active plan requires a configured price and duration; "
                    "subscription plans also require a billing period in days"
                ),
            )
        return configuration

    @staticmethod
    def _validate_mapping_configuration(
        feature: Feature,
        is_enabled: bool,
        configuration: dict[str, Any],
    ) -> None:
        if not isinstance(configuration, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Configuration must be a JSON object",
            )
        if is_enabled and not feature.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Inactive features cannot be enabled in a plan",
            )
        if feature.feature_type == "limit" and is_enabled:
            limit = configuration.get("limit")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or limit < 0
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Enabled limit features require a non-negative numeric limit",
                )

    def _get_plan(self, plan_id: UUID) -> Plan:
        plan = self.repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        return plan

    def _get_feature(self, feature_id: UUID) -> Feature:
        feature = self.repository.get_feature(feature_id)
        if feature is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Feature not found"
            )
        return feature

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Catalog resource conflicts with existing data",
            ) from error

    @staticmethod
    def _plan_read(
        plan: Plan, mappings: list[tuple[PlanFeature, Feature]]
    ) -> PlanRead:
        return PlanRead(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            description=plan.description,
            billing_type=plan.billing_type,
            duration_type=plan.duration_type,
            duration_days=plan.duration_days,
            billing_period_days=plan.billing_period_days,
            price=plan.price,
            currency=plan.currency,
            is_active=plan.is_active,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            features=[
                CatalogAdminService._mapping_read(mapping, feature)
                for mapping, feature in mappings
            ],
        )

    @staticmethod
    def _feature_read(
        feature: Feature, mappings: list[tuple[PlanFeature, Plan]]
    ) -> FeatureRead:
        return FeatureRead(
            id=feature.id,
            code=feature.code,
            name=feature.name,
            description=feature.description,
            feature_type=feature.feature_type,
            is_active=feature.is_active,
            created_at=feature.created_at,
            updated_at=feature.updated_at,
            plans=[
                FeaturePlanMappingRead(
                    id=mapping.id,
                    is_enabled=mapping.is_enabled,
                    configuration=mapping.configuration,
                    plan=PlanSummary.model_validate(plan),
                )
                for mapping, plan in mappings
            ],
        )

    @staticmethod
    def _mapping_read(mapping: PlanFeature, feature: Feature) -> PlanFeatureRead:
        return PlanFeatureRead(
            id=mapping.id,
            plan_id=mapping.plan_id,
            feature_id=mapping.feature_id,
            is_enabled=mapping.is_enabled,
            configuration=mapping.configuration,
            feature=FeatureSummary.model_validate(feature),
        )
