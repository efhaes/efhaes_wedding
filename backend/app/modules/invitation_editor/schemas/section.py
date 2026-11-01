import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_RESERVED_KEYS = {
    "id",
    "invitation_id",
    "owner_id",
    "workspace_id",
    "section_id",
    "section_key",
    "section_type",
    "sort_order",
    "is_enabled",
}


class SectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    section_key: str
    section_type: str
    title: str
    content: dict[str, Any]
    is_enabled: bool
    sort_order: int
    updated_at: datetime


class SectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]
    expected_updated_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _RESERVED_KEYS.intersection(value):
            raise ValueError("Section content cannot change section or ownership fields")

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        try:
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Section content must contain valid JSON values") from error
        if len(encoded.encode("utf-8")) > 32_768:
            raise ValueError("Section content must not exceed 32 KiB")
        return value


class SectionEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool
    expected_updated_at: datetime | None = None


class SectionReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[UUID] = Field(max_length=30)

    @model_validator(mode="after")
    def unique_section_ids(self) -> "SectionReorder":
        if len(self.section_ids) != len(set(self.section_ids)):
            raise ValueError("Section IDs must not contain duplicates")
        return self
