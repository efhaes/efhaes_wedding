import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


InvitationStatus = Literal["draft", "published", "archived"]
InvitationSortBy = Literal["created_at", "updated_at"]
InvitationSortOrder = Literal["asc", "desc"]


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    template_id: UUID
    slug: str | None = Field(default=None, min_length=1, max_length=180)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must not be blank")
        return normalized

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
            raise ValueError("Slug must contain lowercase letters, numbers, and hyphens")
        return normalized


class PersonalInvitationCreate(InvitationCreate):
    pass


class WorkspaceInvitationCreate(InvitationCreate):
    model_config = ConfigDict(extra="forbid")

    client_id: UUID | None = None


class InvitationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    template_id: UUID | None = None
    client_id: UUID | None = None
    slug: str | None = Field(default=None, min_length=1, max_length=180)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must not be blank")
        return normalized

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
            raise ValueError("Slug must contain lowercase letters, numbers, and hyphens")
        return normalized

    @model_validator(mode="after")
    def require_mutable_fields(self) -> "InvitationUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        if "template_id" in self.model_fields_set and self.template_id is None:
            raise ValueError("Template ID cannot be null")
        if "slug" in self.model_fields_set and self.slug is None:
            raise ValueError("Slug cannot be null")
        return self


class InvitationListItem(BaseModel):
    id: UUID
    title: str
    slug: str
    owner_id: UUID | None
    workspace_id: UUID | None
    client_id: UUID | None
    template_id: UUID
    status: InvitationStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvitationRead(InvitationListItem):
    first_published_at: datetime | None
    expires_at: datetime | None


class InvitationPage(BaseModel):
    items: list[InvitationListItem]
    page: int
    page_size: int
    total: int
