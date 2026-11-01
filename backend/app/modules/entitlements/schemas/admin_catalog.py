import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.entitlements.schemas.catalog import BillingType, DurationType


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    billing_type: BillingType
    duration_type: DurationType
    duration_days: int | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool = False
    billing_period_days: int | None = Field(default=None, gt=0)


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    duration_type: DurationType | None = None
    duration_days: int | None = Field(default=None, gt=0)
    billing_period_days: int | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool | None = None


class FeatureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    feature_type: Literal["boolean", "limit"]
    is_active: bool = False


class FeatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None


class PlanFeatureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: UUID
    is_enabled: bool = False
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("configuration")
    @classmethod
    def validate_json_configuration(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Configuration must contain valid JSON values") from error
        return value


class PlanFeatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    configuration: dict[str, Any] | None = None

    @field_validator("configuration")
    @classmethod
    def validate_json_configuration(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return value
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Configuration must contain valid JSON values") from error
        return value


class FeatureSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    feature_type: str
    is_active: bool


class PlanSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str


class PlanFeatureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    feature_id: UUID
    is_enabled: bool
    configuration: dict[str, Any]
    feature: FeatureSummary


class FeaturePlanMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_enabled: bool
    configuration: dict[str, Any]
    plan: PlanSummary


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None
    billing_type: BillingType
    duration_type: DurationType
    duration_days: int | None
    billing_period_days: int | None
    price: Decimal | None
    currency: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    features: list[PlanFeatureRead]


class FeatureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None
    feature_type: Literal["boolean", "limit"]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    plans: list[FeaturePlanMappingRead]


class PlanPage(BaseModel):
    items: list[PlanRead]
    page: int
    page_size: int
    total: int


class FeaturePage(BaseModel):
    items: list[FeatureRead]
    page: int
    page_size: int
    total: int
