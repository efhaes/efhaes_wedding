from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
)
from app.modules.billing.schemas.transactions import (
    PaymentRead,
    PersonalPurchaseCreate,
    PersonalPurchaseRead,
    WorkspaceSubscriptionCreate,
    WorkspaceSubscriptionRead,
)
from app.modules.billing.services.billing_service import BillingService
from app.modules.users.models.user import User


router = APIRouter(tags=["billing"])


@router.post(
    "/purchases",
    response_model=PersonalPurchaseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create a pending personal purchase",
    description=(
        "Creates a pending purchase and payment using the active plan's server-side "
        "price, duration, and feature snapshot. This endpoint cannot activate access."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "CSRF validation required"},
        404: {"description": "Plan not found"},
        409: {"description": "Plan is inactive or is not configured for purchase"},
        422: {"description": "Invalid request"},
    },
)
def create_personal_purchase(
    payload: PersonalPurchaseCreate,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
) -> PersonalPurchaseRead:
    return BillingService(session).create_personal_purchase(user, payload.plan_id)


@router.get(
    "/purchases/{purchase_id}",
    response_model=PersonalPurchaseRead,
    summary="Get a personal purchase",
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Purchase not found"},
    },
)
def get_personal_purchase(
    purchase_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
) -> PersonalPurchaseRead:
    return BillingService(session).get_personal_purchase(user, purchase_id)


@router.post(
    "/subscriptions",
    response_model=WorkspaceSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create a pending workspace subscription",
    description=(
        "Only an active workspace owner or admin may start a subscription. "
        "The plan must define an explicit positive billing period in days. "
        "The pending subscription grants no entitlement."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "CSRF or workspace billing authorization required"},
        404: {"description": "Workspace or plan not found"},
        409: {"description": "Workspace or plan is not available"},
        422: {"description": "Invalid request"},
    },
)
def create_workspace_subscription(
    payload: WorkspaceSubscriptionCreate,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
) -> WorkspaceSubscriptionRead:
    return BillingService(session).create_workspace_subscription(
        user, payload.workspace_id, payload.plan_id
    )


@router.get(
    "/subscriptions/{subscription_id}",
    response_model=WorkspaceSubscriptionRead,
    summary="Get a workspace subscription",
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Subscription not found"},
    },
)
def get_workspace_subscription(
    subscription_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
) -> WorkspaceSubscriptionRead:
    return BillingService(session).get_workspace_subscription(user, subscription_id)


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentRead,
    summary="Get a payment status",
    description=(
        "Read-only. Payment status and amount cannot be changed through this API."
    ),
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Payment not found"},
    },
)
def get_payment(
    payment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
) -> PaymentRead:
    return BillingService(session).get_payment(user, payment_id)
