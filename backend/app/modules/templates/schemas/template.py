import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.modules.guestbook.schemas.guestbook import GuestbookSectionConfiguration
from app.modules.rsvp.schemas.rsvp import RsvpSectionConfiguration


AccessLevel = Literal["free", "premium"]
TemplateStatus = Literal["draft", "published", "archived"]
SectionType = Literal[
    "cover",
    "couple_info",
    "event",
    "love_story",
    "gallery",
    "location",
    "rsvp",
    "guestbook",
    "gift",
    "closing",
]
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_PALETTE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
SortBy = Literal["name", "created_at"]
SortOrder = Literal["asc", "desc"]


def validate_design_config(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) - {"colors", "palette"}:
        raise ValueError("Design configuration contains unsupported fields")

    def validate_color_map(color_map: Any) -> None:
        if not isinstance(color_map, dict) or set(color_map) != {"primary"}:
            raise ValueError("Color configuration must contain only primary")
        primary = color_map["primary"]
        if not isinstance(primary, str) or not _HEX_COLOR.fullmatch(primary):
            raise ValueError("Primary color must be a 3- or 6-digit hex color")

    if "colors" in value:
        validate_color_map(value["colors"])
    if "palette" in value:
        palette = value["palette"]
        if isinstance(palette, dict):
            validate_color_map(palette)
        elif not isinstance(palette, str) or not _PALETTE_NAME.fullmatch(palette):
            raise ValueError("Palette must be a simple name or primary color map")
    return value


class DefaultSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    type: SectionType
    title: str = Field(min_length=1, max_length=120)
    is_enabled: bool = True
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Section title must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_configuration(self) -> "DefaultSection":
        configuration = self.configuration
        if self.type == "rsvp":
            RsvpSectionConfiguration.model_validate(configuration)
            allowed = {
                "allow_response_changes",
                "max_attendees_per_guest",
                "deadline_at",
            }
        elif self.type == "guestbook":
            GuestbookSectionConfiguration.model_validate(configuration)
            allowed = {
                "moderation_required",
                "allow_guest_message",
                "allow_anonymous",
            }
        else:
            return self
        if set(configuration) - allowed:
            raise ValueError(f"Unsupported {self.type} section configuration field")
        return self


class TemplateFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = Field(default=None, max_length=2000)
    category: str = Field(min_length=1, max_length=80)
    access_level: AccessLevel = "free"
    thumbnail_url: AnyHttpUrl | None = None
    preview_image_url: AnyHttpUrl | None = None
    design_config: dict[str, Any] = Field(default_factory=dict)
    default_sections: list[DefaultSection] = Field(default_factory=list, max_length=30)

    @field_validator("design_config")
    @classmethod
    def validate_design_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_design_config(value)

    @field_validator("name", "category")
    @classmethod
    def normalize_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def unique_section_keys(self) -> "TemplateFields":
        keys = [section.key for section in self.default_sections]
        if len(keys) != len(set(keys)):
            raise ValueError("Default section keys must be unique")
        return self


class TemplateAdminCreate(TemplateFields):
    pass


class TemplateAdminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    access_level: AccessLevel | None = None
    thumbnail_url: AnyHttpUrl | None = None
    preview_image_url: AnyHttpUrl | None = None
    design_config: dict[str, Any] | None = None
    default_sections: list[DefaultSection] | None = Field(default=None, max_length=30)

    @field_validator("design_config")
    @classmethod
    def validate_design_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_design_config(value) if value is not None else None

    @field_validator("name", "category")
    @classmethod
    def normalize_nonblank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_changes(self) -> "TemplateAdminUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        nullable_fields = {"description", "thumbnail_url", "preview_image_url"}
        invalid_null_fields = {
            field
            for field in self.model_fields_set
            if field not in nullable_fields and getattr(self, field) is None
        }
        if invalid_null_fields:
            raise ValueError(
                f"Fields cannot be null: {', '.join(sorted(invalid_null_fields))}"
            )
        if self.default_sections is not None:
            keys = [section.key for section in self.default_sections]
            if len(keys) != len(set(keys)):
                raise ValueError("Default section keys must be unique")
        return self


class TemplateCatalogRead(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    category: str
    access_level: AccessLevel
    thumbnail_url: str | None
    preview_image_url: str | None
    created_at: datetime


class TemplateAdminRead(TemplateCatalogRead):
    status: TemplateStatus
    is_active: bool
    version: int
    updated_at: datetime
    design_config: dict[str, Any]
    default_sections: list[DefaultSection]


class TemplatePreview(TemplateCatalogRead):
    design_config: dict[str, Any] = Field(
        description="Validated public design settings for rendering the template."
    )
    default_sections: list[DefaultSection] = Field(
        description="Validated default sections; excludes template administration fields."
    )


class TemplateCatalogPage(BaseModel):
    items: list[TemplateCatalogRead]
    page: int
    page_size: int
    total: int


class TemplateAdminPage(BaseModel):
    items: list[TemplateAdminRead]
    page: int
    page_size: int
    total: int
