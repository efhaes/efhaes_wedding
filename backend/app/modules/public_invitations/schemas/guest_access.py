from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GuestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)

    @field_validator("name", "category")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            if value == "":
                return None
            raise ValueError("Value must not be blank")
        return normalized


class GuestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)

    @field_validator("name", "category")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_changes(self) -> "GuestUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        return self


class GuestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    category: str | None
    created_at: datetime
    updated_at: datetime


class GuestPage(BaseModel):
    items: list[GuestRead]
    page: int
    page_size: int
    total: int


class GuestLinkRead(BaseModel):
    guest: GuestRead
    invitation_url: str


class GuestLinkRegenerated(BaseModel):
    invitation_url: str
