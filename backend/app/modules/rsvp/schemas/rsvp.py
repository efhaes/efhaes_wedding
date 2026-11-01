from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.rsvp import RSVP


PublicAttendanceStatus = Literal["attending", "not_attending"]
RsvpAttendanceStatus = Literal["attending", "not_attending", "pending"]
RsvpStatusFilter = Literal["attending", "not_attending", "pending"]


class PublicRsvpSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guest_token: str | None = Field(default=None, min_length=32, max_length=160)
    attendance_status: PublicAttendanceStatus
    number_of_attendees: int | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=1000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_attendee_count(self) -> "PublicRsvpSubmission":
        if self.attendance_status == "attending":
            if self.number_of_attendees is None or self.number_of_attendees < 1:
                raise ValueError("Attending guests must provide at least one attendee")
        elif self.number_of_attendees not in (None, 0):
            raise ValueError("Non-attending guests must have zero attendees")
        return self


class RsvpRead(BaseModel):
    id: UUID
    invitation_id: UUID
    guest_id: UUID | None
    guest_name: str
    attendance_status: RsvpAttendanceStatus
    number_of_attendees: int
    message: str | None
    responded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicRsvpRead(BaseModel):
    attendance_status: PublicAttendanceStatus
    number_of_attendees: int
    message: str | None
    responded_at: datetime


class RsvpPage(BaseModel):
    items: list[RsvpRead]
    page: int
    page_size: int
    total: int


class RsvpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attendance_status: PublicAttendanceStatus | None = None
    number_of_attendees: int | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=1000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_update(self) -> "RsvpUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one RSVP field must be provided")
        if any(
            getattr(self, field) is None
            for field in self.model_fields_set
            if field != "message"
        ):
            raise ValueError("RSVP status and attendee count cannot be null")
        if self.attendance_status == "attending" and self.number_of_attendees == 0:
            raise ValueError("Attending guests must have at least one attendee")
        if (
            self.attendance_status == "not_attending"
            and self.number_of_attendees not in (None, 0)
        ):
            raise ValueError("Non-attending guests must have zero attendees")
        return self


class RsvpSectionConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow")

    allow_response_changes: bool = False
    max_attendees_per_guest: int | None = Field(default=None, ge=1, le=100)
    deadline_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("RSVP deadline must include a timezone")
        return value


class RsvpSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    allow_response_changes: bool | None = None
    max_attendees_per_guest: int | None = Field(default=None, ge=1, le=100)
    deadline_at: datetime | None = None
    expected_updated_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("RSVP deadline must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_settings(self) -> "RsvpSettingsUpdate":
        changes = self.model_fields_set - {"expected_updated_at"}
        if not changes:
            raise ValueError("At least one RSVP setting must be provided")
        if any(getattr(self, field) is None for field in changes - {"deadline_at"}):
            raise ValueError("RSVP settings cannot be null")
        return self


class RsvpSettingsRead(BaseModel):
    is_enabled: bool
    allow_response_changes: bool
    max_attendees_per_guest: int
    deadline_at: datetime | None


def rsvp_read_data(
    rsvp: RSVP,
    *,
    guest_name: str | None = None,
) -> dict[str, Any]:
    is_declined = rsvp.attendance_status == "declined"
    return {
        "id": rsvp.id,
        "invitation_id": rsvp.invitation_id,
        "guest_id": rsvp.guest_id,
        "guest_name": guest_name or rsvp.guest_name,
        "attendance_status": (
            "not_attending" if is_declined else rsvp.attendance_status
        ),
        "number_of_attendees": 0 if is_declined else rsvp.guest_count,
        "message": rsvp.message,
        "responded_at": rsvp.responded_at,
        "created_at": rsvp.created_at,
        "updated_at": rsvp.updated_at,
    }
