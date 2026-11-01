from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DurationType = Literal["pending", "fixed", "lifetime", "recurring"]
BillingType = Literal["one_time", "subscription"]


class PlanConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    billing_type: BillingType
    duration_type: DurationType
    duration_days: int | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool = False
    billing_period_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_configuration(self) -> "PlanConfiguration":
        if self.billing_type == "one_time":
            valid_duration = (
                self.duration_type == "pending" and self.duration_days is None
            ) or (
                self.duration_type == "fixed"
                and self.duration_days is not None
                and self.duration_days > 0
            ) or (
                self.duration_type == "lifetime" and self.duration_days is None
            )
            if self.billing_period_days is not None:
                raise ValueError("One-time plans cannot define a billing period")
        else:
            valid_duration = (
                self.duration_type == "recurring" and self.duration_days is None
            )
        if not valid_duration:
            raise ValueError("Duration configuration does not match the billing type")

        if self.price is None:
            if self.currency is not None:
                raise ValueError("Currency requires a configured price")
        elif self.price > 0 and self.currency is None:
            raise ValueError("A priced plan requires a currency")
        if self.is_active and (
            self.price is None
            or self.duration_type == "pending"
            or (
                self.billing_type == "subscription"
                and self.billing_period_days is None
            )
        ):
            raise ValueError("An active plan requires complete billing configuration")
        return self
