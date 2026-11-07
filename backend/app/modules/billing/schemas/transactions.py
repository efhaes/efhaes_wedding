from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PersonalPurchaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID


class WorkspaceSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    plan_id: UUID


class PaymentRead(BaseModel):
    id: UUID
    personal_purchase_id: UUID | None
    subscription_id: UUID | None
    amount: Decimal
    currency: str
    status: str
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CheckoutResponse(BaseModel):
    payment_id: UUID
    attempt_id: UUID
    provider_order_id: str
    status: Literal["creating", "ready", "indeterminate", "failed", "resolved"]
    token: str | None = None
    redirect_url: str | None = None


class PersonalPurchaseRead(BaseModel):
    id: UUID
    plan_id: UUID
    status: str
    purchased_price: Decimal | None
    purchased_currency: str | None
    duration_type_snapshot: str | None
    duration_days_snapshot: int | None
    activated_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    payment: PaymentRead | None


class WorkspaceSubscriptionRead(BaseModel):
    id: UUID
    workspace_id: UUID
    plan_id: UUID
    status: str
    purchased_price: Decimal | None
    purchased_currency: str | None
    billing_period_days_snapshot: int | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    created_at: datetime
    updated_at: datetime
    payment: PaymentRead | None
