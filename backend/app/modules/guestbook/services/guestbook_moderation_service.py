from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.guestbook.repositories.guestbook_repository import (
    GuestbookRepository,
)
from app.modules.guestbook.schemas.guestbook import (
    GuestbookEntryRead,
    GuestbookSettingsRead,
    GuestbookSettingsUpdate,
    GuestbookSectionConfiguration,
    ModerationStatus,
    guestbook_read_data,
)
from app.modules.guestbook.services.guestbook_configuration import (
    commit_guestbook,
    same_timestamp,
    section_configuration,
)
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import (
    InvitationRepository,
)
from app.modules.users.models.user import User
from pydantic import ValidationError


class GuestbookModerationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = GuestbookRepository(session)
        self.invitations = InvitationRepository(session)

    def moderate_personal(
        self,
        user: User,
        invitation_id: UUID,
        entry_id: UUID,
        status_value: ModerationStatus,
    ) -> GuestbookEntryRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._moderate(invitation.id, entry_id, status_value, user.id)

    def moderate_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        entry_id: UUID,
        status_value: ModerationStatus,
        user: User,
    ) -> GuestbookEntryRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._moderate(invitation.id, entry_id, status_value, user.id)

    def delete_personal(
        self,
        user: User,
        invitation_id: UUID,
        entry_id: UUID,
    ) -> None:
        invitation = self._personal_invitation(user.id, invitation_id)
        self._delete(invitation.id, entry_id)

    def delete_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        entry_id: UUID,
    ) -> None:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        self._delete(invitation.id, entry_id)

    def get_settings_personal(
        self,
        user: User,
        invitation_id: UUID,
    ) -> GuestbookSettingsRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._settings(invitation.id)

    def get_settings_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> GuestbookSettingsRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._settings(invitation.id)

    def update_settings_personal(
        self,
        user: User,
        invitation_id: UUID,
        payload: GuestbookSettingsUpdate,
    ) -> GuestbookSettingsRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._update_settings(invitation, payload)

    def update_settings_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        payload: GuestbookSettingsUpdate,
    ) -> GuestbookSettingsRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._update_settings(invitation, payload)

    def _moderate(
        self,
        invitation_id: UUID,
        entry_id: UUID,
        status_value: ModerationStatus,
        moderator_id: UUID,
    ) -> GuestbookEntryRead:
        entry = self.repository.get_entry(invitation_id, entry_id, lock=True)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guestbook entry not found",
            )
        entry.status = status_value
        entry.is_approved = status_value == "approved"
        entry.moderated_at = datetime.now(timezone.utc)
        entry.moderated_by = moderator_id
        commit_guestbook(self.session)
        return GuestbookEntryRead.model_validate(guestbook_read_data(entry))

    def _delete(self, invitation_id: UUID, entry_id: UUID) -> None:
        entry = self.repository.get_entry(invitation_id, entry_id, lock=True)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guestbook entry not found",
            )
        self.session.delete(entry)
        commit_guestbook(self.session)

    def _settings(self, invitation_id: UUID) -> GuestbookSettingsRead:
        section = self.repository.get_section(invitation_id)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guestbook section not found",
            )
        configuration = section_configuration(section)
        return GuestbookSettingsRead(
            is_enabled=bool(section and section.is_enabled),
            moderation_required=configuration.moderation_required,
            allow_guest_message=configuration.allow_guest_message,
            allow_anonymous=configuration.allow_anonymous,
        )

    def _update_settings(
        self,
        invitation: Invitation,
        payload: GuestbookSettingsUpdate,
    ) -> GuestbookSettingsRead:
        invitation = self._lock_invitation(invitation)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        section = self.repository.get_section(invitation.id, lock=True)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guestbook section not found",
            )
        if payload.expected_updated_at is not None and not same_timestamp(
            payload.expected_updated_at,
            section.updated_at,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Guestbook settings have changed; reload and try again",
            )
        changes = payload.model_dump(
            exclude_unset=True,
            exclude={"expected_updated_at"},
        )
        if any(value is None for value in changes.values()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Guestbook settings cannot be null",
            )
        content = dict(section.content)
        content.update(changes)
        try:
            GuestbookSectionConfiguration.model_validate(content)
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Guestbook section configuration is invalid",
            ) from error
        section.content = content
        section.updated_at = datetime.now(timezone.utc)
        commit_guestbook(self.session)
        return self._settings(invitation.id)

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

    def _personal_invitation(
        self,
        owner_id: UUID,
        invitation_id: UUID,
    ) -> Invitation:
        invitation = self.invitations.find_personal(owner_id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return invitation

    def _workspace_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> Invitation:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return invitation
