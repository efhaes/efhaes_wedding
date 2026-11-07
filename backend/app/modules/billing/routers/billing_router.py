from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
)
from app.modules.billing.schemas.transactions import (
    CheckoutResponse,
    PaymentRead,
    PersonalPurchaseCreate,
    PersonalPurchaseRead,
    WorkspaceSubscriptionCreate,
    WorkspaceSubscriptionRead,
)
from app.modules.billing.services.billing_service import BillingService
from app.modules.billing.services.midtrans_checkout_service import (
    MidtransCheckoutService,
)
from app.modules.billing.services.midtrans_client import MidtransSnapClient
from app.modules.billing.services.midtrans_verification_service import (
    MidtransProviderVerifier,
)
from app.modules.billing.services.midtrans_webhook_service import (
    MidtransWebhookService,
)
from app.modules.users.models.user import User


router = APIRouter(tags=["billing"])


@router.post(
    "/payments/{payment_id}/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(require_csrf)],
    summary="Start or resume Midtrans Snap checkout",
    responses={
        202: {"description": "Checkout is being created or reconciled"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF or workspace billing authorization required"},
        404: {"description": "Payment not found"},
        409: {"description": "Payment or checkout attempt is not eligible"},
        503: {"description": "Midtrans is unavailable or not configured"},
    },
)
def checkout_payment(
    payment_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=16, max_length=255)
    ],
) -> CheckoutResponse:
    response.headers["Cache-Control"] = "no-store"
    checkout, response.status_code = MidtransCheckoutService(
        session, settings
    ).checkout(user, payment_id, idempotency_key)
    return checkout


@router.post(
    "/payments/midtrans/webhook",
    summary="Receive a Midtrans transaction notification",
    responses={
        200: {"description": "Notification accepted or queued for manual review"},
        400: {"description": "Invalid notification"},
        404: {"description": "Payment not found"},
        503: {"description": "Notification could not be verified or persisted"},
    },
)
def receive_midtrans_webhook(
    payload: dict[str, object],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    try:
        client = MidtransSnapClient(settings)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Midtrans webhook processing is not configured",
        ) from error
    verifier = MidtransProviderVerifier(settings, client)
    return MidtransWebhookService(session, settings, verifier).process(payload)


@router.post(
    "/purchases",
    response_model=PersonalPurchaseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create a pending personal purchase",
    description=(
        "Creates a pending purchase and payment using the active plan's server-side "
        "price, duration, and feature snapshot. This endpoint cannot activate access. "
        "An Idempotency-Key replays the original purchase; a different key starts "
        "a new intentional purchase."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "CSRF validation required"},
        404: {"description": "Plan not found"},
        409: {"description": "Plan is inactive or is not configured for purchase"},
        422: {"description": "Invalid request or Idempotency-Key"},
    },
)
def create_personal_purchase(
    payload: PersonalPurchaseCreate,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=16, max_length=255)
    ],
) -> PersonalPurchaseRead:
    return BillingService(session).create_personal_purchase(
        user, payload.plan_id, idempotency_key
    )


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
        "The pending subscription grants no entitlement. An Idempotency-Key replays "
        "the original subscription request."
    ),
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "CSRF or workspace billing authorization required"},
        404: {"description": "Workspace or plan not found"},
        409: {"description": "Workspace or plan is not available"},
        422: {"description": "Invalid request or Idempotency-Key"},
    },
)
def create_workspace_subscription(
    payload: WorkspaceSubscriptionCreate,
    session: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_authenticated_user)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=16, max_length=255)
    ],
) -> WorkspaceSubscriptionRead:
    return BillingService(session).create_workspace_subscription(
        user, payload.workspace_id, payload.plan_id, idempotency_key
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
