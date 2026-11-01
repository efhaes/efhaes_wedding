from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation_section import InvitationSection
from app.modules.rsvp.schemas.rsvp import (
    PublicRsvpSubmission,
    RsvpSectionConfiguration,
)


@dataclass(frozen=True)
class EffectiveRsvpConfiguration:
    allow_response_changes: bool
    max_attendees_per_guest: int
    deadline_at: datetime | None


def effective_configuration(
    section: InvitationSection | None,
) -> EffectiveRsvpConfiguration:
    if section is None:
        return EffectiveRsvpConfiguration(
            allow_response_changes=False,
            max_attendees_per_guest=settings.RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST,
            deadline_at=None,
        )
    try:
        configuration = RsvpSectionConfiguration.model_validate(section.content)
    except (ValidationError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RSVP section configuration is invalid",
        ) from error
    max_attendees = (
        configuration.max_attendees_per_guest
        or settings.RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST
    )
    if max_attendees > settings.RSVP_MAX_ATTENDEES_PER_GUEST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RSVP section attendee limit exceeds the system maximum",
        )
    return EffectiveRsvpConfiguration(
        allow_response_changes=configuration.allow_response_changes,
        max_attendees_per_guest=max_attendees,
        deadline_at=configuration.deadline_at,
    )


def ensure_before_deadline(configuration: EffectiveRsvpConfiguration) -> None:
    deadline = configuration.deadline_at
    if deadline is not None and datetime.now(timezone.utc) > deadline.astimezone(
        timezone.utc
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The RSVP deadline has passed",
        )


def database_status(status_value: str) -> str:
    return "declined" if status_value == "not_attending" else status_value


def submission_count(payload: PublicRsvpSubmission) -> int:
    if payload.attendance_status == "not_attending":
        return 0
    return int(payload.number_of_attendees or 0)


def commit_rsvp(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RSVP operation conflicted with existing data",
        ) from error
    except SQLAlchemyError:
        session.rollback()
        raise


def ensure_not_archived(invitation_status: str) -> None:
    if invitation_status == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived invitations cannot update RSVP settings",
        )


def same_timestamp(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left == right
