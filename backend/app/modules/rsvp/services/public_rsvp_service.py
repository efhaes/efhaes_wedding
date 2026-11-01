from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.rsvp import RSVP
from app.modules.public_invitations.services.public_invitation_service import (
    guest_token_hash,
)
from app.modules.rsvp.repositories.rsvp_repository import RsvpRepository
from app.modules.rsvp.schemas.rsvp import (
    PublicRsvpSubmission,
    PublicRsvpRead,
)
from app.modules.rsvp.services.rsvp_configuration import (
    commit_rsvp,
    database_status,
    effective_configuration,
    ensure_before_deadline,
    submission_count,
)


class PublicRsvpService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = RsvpRepository(session)

    def submit(self, slug: str, payload: PublicRsvpSubmission) -> PublicRsvpRead:
        invitation = self.repository.find_public_invitation(slug, lock=True)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        section = self.repository.get_rsvp_section(invitation.id, lock=True)
        configuration = effective_configuration(section)
        if section is None or not section.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="RSVP is not available for this invitation",
            )
        ensure_before_deadline(configuration)
        if payload.guest_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Guest token is required",
            )
        guest = self.repository.find_guest_by_token(
            invitation.id,
            guest_token_hash(payload.guest_token),
            lock=True,
        )
        if guest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation or guest token not found",
            )
        attendee_count = submission_count(payload)
        if attendee_count > configuration.max_attendees_per_guest:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Attendee count exceeds this invitation's limit",
            )
        rsvp = self.repository.get_for_guest(invitation.id, guest.id, lock=True)
        if rsvp is not None and not configuration.allow_response_changes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="RSVP responses can no longer be changed",
            )
        now = datetime.now(timezone.utc)
        if rsvp is None:
            rsvp = RSVP(
                invitation_id=invitation.id,
                guest_id=guest.id,
                guest_name=guest.name,
                attendance_status=database_status(payload.attendance_status),
                guest_count=attendee_count,
                message=payload.message,
                responded_at=now,
            )
            self.session.add(rsvp)
        else:
            rsvp.guest_name = guest.name
            rsvp.attendance_status = database_status(payload.attendance_status)
            rsvp.guest_count = attendee_count
            rsvp.message = payload.message
            rsvp.responded_at = now
        commit_rsvp(self.session)
        return PublicRsvpRead(
            attendance_status=payload.attendance_status,
            number_of_attendees=rsvp.guest_count,
            message=rsvp.message,
            responded_at=now,
        )
