from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_csrf, require_platform_admin
from app.modules.entitlements.schemas.admin_catalog import (
    FeatureCreate,
    FeaturePage,
    FeatureRead,
    FeatureUpdate,
    PlanCreate,
    PlanFeatureCreate,
    PlanFeatureRead,
    PlanFeatureUpdate,
    PlanPage,
    PlanRead,
    PlanUpdate,
)
from app.modules.entitlements.services.catalog_admin_service import (
    CatalogAdminService,
)


router = APIRouter(
    prefix="/admin",
    tags=["admin-catalog"],
    dependencies=[Depends(require_platform_admin)],
)
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/plans",
    response_model=PlanPage,
    summary="List plan catalog",
    description="Lists active and inactive plans. Requires a platform administrator.",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        422: {"description": "Invalid filter or pagination value"},
    },
)
def list_plans(
    session: Session = Depends(get_db),
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    billing_type: Literal["one_time", "subscription"] | None = None,
    is_active: bool | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: Literal["code", "name", "billing_type", "price", "created_at", "updated_at"] = "code",
    sort_order: Literal["asc", "desc"] = "asc",
) -> PlanPage:
    return CatalogAdminService(session).list_plans(
        search=search,
        billing_type=billing_type,
        is_active=is_active,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.post(
    "/plans",
    response_model=PlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create plan",
    description=(
        "Creates a plan with explicit billing, duration, and (for subscription plans) "
        "billing-period configuration. "
        "Code is immutable after creation; pending or unpriced plans must remain inactive."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        409: {"description": "Plan code already exists"},
        422: {"description": "Invalid plan configuration"},
    },
)
def create_plan(
    payload: PlanCreate,
    session: Session = Depends(get_db),
) -> PlanRead:
    return CatalogAdminService(session).create_plan(payload)


@router.get(
    "/plans/{plan_id}",
    response_model=PlanRead,
    summary="Get plan detail",
    description="Returns plan configuration and its feature mappings.",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Plan not found"},
    },
)
def get_plan(plan_id: UUID, session: Session = Depends(get_db)) -> PlanRead:
    return CatalogAdminService(session).get_plan(plan_id)


@router.patch(
    "/plans/{plan_id}",
    response_model=PlanRead,
    dependencies=[Depends(require_csrf)],
    summary="Update plan",
    description=(
        "Updates mutable plan configuration. Plan code and billing type are immutable. "
        "Plan records are deactivated rather than permanently deleted."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        404: {"description": "Plan not found"},
        409: {"description": "Plan configuration conflicts with existing data"},
        422: {"description": "Invalid plan configuration"},
    },
)
def update_plan(
    plan_id: UUID,
    payload: PlanUpdate,
    session: Session = Depends(get_db),
) -> PlanRead:
    return CatalogAdminService(session).update_plan(plan_id, payload)


@router.get(
    "/plans/{plan_id}/features",
    response_model=list[PlanFeatureRead],
    summary="List features assigned to a plan",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Plan not found"},
    },
)
def list_plan_features(
    plan_id: UUID,
    session: Session = Depends(get_db),
) -> list[PlanFeatureRead]:
    return CatalogAdminService(session).list_plan_features(plan_id)


@router.post(
    "/plans/{plan_id}/features",
    response_model=PlanFeatureRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Assign feature to plan",
    description=(
        "Creates a unique plan-feature mapping. Enabled limit mappings require "
        "a non-negative numeric `configuration.limit`; its unit and enforcement "
        "scope are not defined by this API. Transactions snapshot enabled mappings "
        "at creation; legacy transactions without snapshots prevent mapping changes."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        404: {"description": "Plan or feature not found"},
        409: {"description": "Mapping exists, feature is inactive, or a legacy transaction prevents changes"},
        422: {"description": "Invalid mapping configuration"},
    },
)
def add_plan_feature(
    plan_id: UUID,
    payload: PlanFeatureCreate,
    session: Session = Depends(get_db),
) -> PlanFeatureRead:
    return CatalogAdminService(session).add_plan_feature(plan_id, payload)


@router.patch(
    "/plans/{plan_id}/features/{feature_id}",
    response_model=PlanFeatureRead,
    dependencies=[Depends(require_csrf)],
    summary="Update plan feature mapping",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        404: {"description": "Plan or mapping not found"},
        409: {"description": "Feature is inactive or a legacy transaction prevents changes"},
        422: {"description": "Invalid mapping configuration"},
    },
)
def update_plan_feature(
    plan_id: UUID,
    feature_id: UUID,
    payload: PlanFeatureUpdate,
    session: Session = Depends(get_db),
) -> PlanFeatureRead:
    return CatalogAdminService(session).update_plan_feature(
        plan_id, feature_id, payload
    )


@router.delete(
    "/plans/{plan_id}/features/{feature_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    summary="Remove plan feature mapping",
    description=(
        "Removes the mapping only when no active or pending legacy transaction "
        "without a feature snapshot references the plan."
    ),
    responses={
        204: {"description": "Mapping removed"},
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        404: {"description": "Plan or mapping not found"},
        409: {"description": "Plan has transactions"},
    },
)
def delete_plan_feature(
    plan_id: UUID,
    feature_id: UUID,
    session: Session = Depends(get_db),
) -> Response:
    CatalogAdminService(session).delete_plan_feature(plan_id, feature_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/features",
    response_model=FeaturePage,
    summary="List feature catalog",
    description="Lists active and inactive features with their plan mappings.",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        422: {"description": "Invalid filter or pagination value"},
    },
)
def list_features(
    session: Session = Depends(get_db),
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    is_active: bool | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: Literal["code", "name", "feature_type", "created_at", "updated_at"] = "code",
    sort_order: Literal["asc", "desc"] = "asc",
) -> FeaturePage:
    return CatalogAdminService(session).list_features(
        search=search,
        is_active=is_active,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.post(
    "/features",
    response_model=FeatureRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create feature",
    description="Creates a feature with an immutable stable code.",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        409: {"description": "Feature code already exists"},
        422: {"description": "Invalid feature configuration"},
    },
)
def create_feature(
    payload: FeatureCreate,
    session: Session = Depends(get_db),
) -> FeatureRead:
    return CatalogAdminService(session).create_feature(payload)


@router.get(
    "/features/{feature_id}",
    response_model=FeatureRead,
    summary="Get feature detail",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Feature not found"},
    },
)
def get_feature(feature_id: UUID, session: Session = Depends(get_db)) -> FeatureRead:
    return CatalogAdminService(session).get_feature(feature_id)


@router.patch(
    "/features/{feature_id}",
    response_model=FeatureRead,
    dependencies=[Depends(require_csrf)],
    summary="Update feature",
    description=(
        "Updates name and description. Feature code and type are immutable. "
        "Deactivation is rejected while existing grants or overrides depend on the feature."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role or CSRF validation required"},
        404: {"description": "Feature not found"},
        409: {"description": "Existing transactions or overrides depend on the feature"},
        422: {"description": "Invalid feature configuration"},
    },
)
def update_feature(
    feature_id: UUID,
    payload: FeatureUpdate,
    session: Session = Depends(get_db),
) -> FeatureRead:
    return CatalogAdminService(session).update_feature(feature_id, payload)
