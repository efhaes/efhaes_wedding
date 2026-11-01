import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.guestbook import GuestbookEntry


GuestbookStatus = Literal["pending", "approved", "hidden"]
ModerationStatus = Literal["approved", "hidden"]

_DISALLOWED_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PublicGuestbookSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    guest_token: str | None = Field(default=None, min_length=32, max_length=160)
    guest_name: str | None = Field(default=None, min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("guest_name")
    @classmethod
    def normalize_guest_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized or _DISALLOWED_CONTROLS.search(normalized):
            raise ValueError("Guest name is invalid")
        return normalized

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized or _DISALLOWED_CONTROLS.search(normalized):
            raise ValueError("Message is invalid")
        if len(normalized) > 1000:
            raise ValueError("Message must not exceed 1000 characters")
        return normalized

    @model_validator(mode="after")
    def validate_sender(self) -> "PublicGuestbookSubmission":
        if self.guest_token is not None and self.guest_name is not None:
            raise ValueError("Guest name is resolved from the personalized token")
        return self


class GuestbookModerationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ModerationStatus


class GuestbookSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    moderation_required: bool | None = None
    allow_guest_message: bool | None = None
    allow_anonymous: bool | None = None
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "GuestbookSettingsUpdate":
        if not (self.model_fields_set - {"expected_updated_at"}):
            raise ValueError("At least one guestbook setting must be provided")
        for field in self.model_fields_set - {"expected_updated_at"}:
            if getattr(self, field) is None:
                raise ValueError("Guestbook settings cannot be null")
        return self


class GuestbookEntryRead(BaseModel):
    id: UUID
    invitation_id: UUID
    guest_name: str
    message: str
    status: GuestbookStatus
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GuestbookPage(BaseModel):
    items: list[GuestbookEntryRead]
    page: int
    page_size: int
    total: int


class PublicGuestbookEntryRead(BaseModel):
    guest_name: str
    message: str
    created_at: datetime


class PublicGuestbookPage(BaseModel):
    items: list[PublicGuestbookEntryRead]
    page: int
    page_size: int
    total: int
    has_next: bool


class GuestbookSettingsRead(BaseModel):
    is_enabled: bool
    moderation_required: bool
    allow_guest_message: bool
    allow_anonymous: bool


class GuestbookSectionConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow")

    moderation_required: bool = True
    allow_guest_message: bool = True
    allow_anonymous: bool = False


def guestbook_read_data(entry: GuestbookEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "invitation_id": entry.invitation_id,
        "guest_name": entry.guest_name,
        "message": entry.message,
        "status": _entry_status(entry),
        "moderated_at": entry.moderated_at,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _entry_status(entry: GuestbookEntry) -> str:
    status = getattr(entry, "status", None)
    if status in ("pending", "approved", "hidden"):
        return status
    return "approved" if entry.is_approved else "pending"
