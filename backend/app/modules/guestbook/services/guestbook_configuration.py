from datetime import datetime, timezone

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.invitation_section import InvitationSection
from app.modules.guestbook.schemas.guestbook import GuestbookSectionConfiguration


def section_configuration(
    section: InvitationSection | None,
) -> GuestbookSectionConfiguration:
    if section is None:
        return GuestbookSectionConfiguration()
    try:
        return GuestbookSectionConfiguration.model_validate(section.content)
    except (ValidationError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Guestbook section configuration is invalid",
        ) from error


def ensure_section_enabled(section: InvitationSection | None) -> None:
    if section is None or not section.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Guestbook is not available for this invitation",
        )


def commit_guestbook(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Guestbook operation conflicted with existing data",
        ) from error
    except SQLAlchemyError:
        session.rollback()
        raise


def same_timestamp(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left == right
