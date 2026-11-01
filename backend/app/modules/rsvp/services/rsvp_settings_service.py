from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation_section import InvitationSection
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import (
    InvitationRepository,
)
from app.modules.rsvp.repositories.rsvp_repository import RsvpRepository
from app.modules.rsvp.schemas.rsvp import RsvpSettingsRead, RsvpSettingsUpdate
from app.modules.rsvp.services.rsvp_configuration import (
    commit_rsvp,
    effective_configuration,
    ensure_not_archived,
    same_timestamp,
)
from app.modules.users.models.user import User


class RsvpSettingsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = RsvpRepository(session)
        self.invitations = InvitationRepository(session)

    def get_personal(self, user: User, invitation_id: UUID) -> RsvpSettingsRead:
        invitation = self.invitations.find_personal(user.id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return self._read(invitation.id)

    def get_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> RsvpSettingsRead:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return self._read(invitation.id)

    def update_personal(
        self,
        user: User,
        invitation_id: UUID,
        payload: RsvpSettingsUpdate,
    ) -> RsvpSettingsRead:
        invitation = self.invitations.find_personal(user.id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return self._update(invitation, payload)

    def update_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        payload: RsvpSettingsUpdate,
    ) -> RsvpSettingsRead:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return self._update(invitation, payload)

    def _read(self, invitation_id: UUID) -> RsvpSettingsRead:
        section = self.repository.get_rsvp_section(invitation_id)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RSVP section not found",
            )
        configuration = effective_configuration(section)
        return RsvpSettingsRead(
            is_enabled=section.is_enabled,
            allow_response_changes=configuration.allow_response_changes,
            max_attendees_per_guest=configuration.max_attendees_per_guest,
            deadline_at=configuration.deadline_at,
        )

    def _update(
        self,
        invitation: Invitation,
        payload: RsvpSettingsUpdate,
    ) -> RsvpSettingsRead:
        ensure_not_archived(invitation.status)
        invitation = self._lock_invitation(invitation)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        section = self.repository.get_rsvp_section(invitation.id, lock=True)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RSVP section not found",
            )
        if payload.expected_updated_at is not None and not same_timestamp(
            payload.expected_updated_at,
            section.updated_at,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="RSVP settings have changed; reload and try again",
            )
        changes = payload.model_dump(exclude_unset=True)
        changes.pop("expected_updated_at", None)
        enabled = changes.pop("is_enabled", None)
        content = dict(section.content)
        if "max_attendees_per_guest" in changes:
            max_attendees = changes["max_attendees_per_guest"]
            if max_attendees > settings.RSVP_MAX_ATTENDEES_PER_GUEST:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Attendee limit exceeds the configured system maximum",
                )
            content["max_attendees_per_guest"] = max_attendees
        if "allow_response_changes" in changes:
            content["allow_response_changes"] = changes["allow_response_changes"]
        if "deadline_at" in changes:
            deadline = changes["deadline_at"]
            content["deadline_at"] = deadline.isoformat() if deadline else None
        section.content = content
        if "is_enabled" in payload.model_fields_set:
            if enabled is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RSVP enabled setting cannot be null",
                )
            section.is_enabled = enabled
        section.updated_at = datetime.now(timezone.utc)
        effective_configuration(section)
        commit_rsvp(self.session)
        return self._read(invitation.id)

    def _lock_invitation(self, invitation: Invitation) -> Invitation | None:
        if invitation.owner_id is not None:
            return self.invitations.find_personal(
                invitation.owner_id,
                invitation.id,
                lock=True,
            )
        if invitation.workspace_id is not None:
            return self.invitations.find_workspace(
                invitation.workspace_id,
                invitation.id,
                lock=True,
            )
        return None
