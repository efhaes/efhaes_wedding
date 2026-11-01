from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation_section import InvitationSection
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.invitation_editor.repositories.section_repository import SectionRepository
from app.modules.invitation_editor.schemas.section import (
    SectionEnabledUpdate,
    SectionReorder,
    SectionUpdate,
)
from app.modules.media.repositories.media_repository import MediaRepository
from app.modules.guestbook.schemas.guestbook import GuestbookSectionConfiguration
from app.modules.rsvp.schemas.rsvp import RsvpSectionConfiguration
from app.modules.templates.models.template import Template
from app.modules.templates.schemas.template import DefaultSection
from app.modules.users.models.user import User


class SectionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.invitations = InvitationRepository(session)
        self.sections = SectionRepository(session)
        self.media = MediaRepository(session)

    def list_personal(self, user: User, invitation_id: UUID) -> list[InvitationSection]:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self.sections.list_for_invitation(invitation.id)

    def get_personal(
        self, user: User, invitation_id: UUID, section_id: UUID
    ) -> InvitationSection:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._section(invitation.id, section_id)

    def list_workspace(
        self, workspace_id: UUID, invitation_id: UUID
    ) -> list[InvitationSection]:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self.sections.list_for_invitation(invitation.id)

    def get_workspace(
        self, workspace_id: UUID, invitation_id: UUID, section_id: UUID
    ) -> InvitationSection:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._section(invitation.id, section_id)

    def update_personal(
        self,
        user: User,
        invitation_id: UUID,
        section_id: UUID,
        payload: SectionUpdate,
    ) -> InvitationSection:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._update(invitation, section_id, payload.content, payload.expected_updated_at)

    def update_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        section_id: UUID,
        payload: SectionUpdate,
    ) -> InvitationSection:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._update(invitation, section_id, payload.content, payload.expected_updated_at)

    def set_enabled_personal(
        self,
        user: User,
        invitation_id: UUID,
        section_id: UUID,
        payload: SectionEnabledUpdate,
    ) -> InvitationSection:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._set_enabled(invitation, section_id, payload)

    def set_enabled_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        section_id: UUID,
        payload: SectionEnabledUpdate,
    ) -> InvitationSection:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._set_enabled(invitation, section_id, payload)

    def reorder_personal(
        self, user: User, invitation_id: UUID, payload: SectionReorder
    ) -> list[InvitationSection]:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._reorder(invitation, payload)

    def reorder_workspace(
        self, workspace_id: UUID, invitation_id: UUID, payload: SectionReorder
    ) -> list[InvitationSection]:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._reorder(invitation, payload)

    def reset_personal(
        self, user: User, invitation_id: UUID, section_id: UUID
    ) -> InvitationSection:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._reset(invitation, section_id)

    def reset_workspace(
        self, workspace_id: UUID, invitation_id: UUID, section_id: UUID
    ) -> InvitationSection:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._reset(invitation, section_id)

    def _personal_invitation(self, owner_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_personal(owner_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def _workspace_invitation(self, workspace_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def _section(self, invitation_id: UUID, section_id: UUID) -> InvitationSection:
        section = self.sections.get_for_invitation(invitation_id, section_id)
        if section is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
        return section

    def _update(
        self,
        invitation: Invitation,
        section_id: UUID,
        content: dict[str, Any],
        expected_updated_at: datetime | None,
    ) -> InvitationSection:
        invitation = self._lock_invitation(invitation)
        self._ensure_editable(invitation)
        section = self.sections.get_for_invitation(invitation.id, section_id, lock=True)
        self._check_section_and_version(section, expected_updated_at)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Section not found",
            )
        if section.section_key == "rsvp" and section.section_type == "rsvp":
            try:
                configuration = RsvpSectionConfiguration.model_validate(content)
            except (ValidationError, TypeError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RSVP section configuration is invalid",
                ) from error
            if (
                configuration.max_attendees_per_guest is not None
                and configuration.max_attendees_per_guest
                > settings.RSVP_MAX_ATTENDEES_PER_GUEST
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Attendee limit exceeds the configured system maximum",
                )
        if section.section_key == "guestbook" and section.section_type == "guestbook":
            try:
                GuestbookSectionConfiguration.model_validate(content)
            except (ValidationError, TypeError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Guestbook section configuration is invalid",
                ) from error
        self._validate_media_references(invitation.id, content)
        section.content = content
        section.updated_at = _next_timestamp(section.updated_at)
        self._commit()
        return section

    def _set_enabled(
        self,
        invitation: Invitation,
        section_id: UUID,
        payload: SectionEnabledUpdate,
    ) -> InvitationSection:
        invitation = self._lock_invitation(invitation)
        self._ensure_editable(invitation)
        section = self.sections.get_for_invitation(invitation.id, section_id, lock=True)
        self._check_section_and_version(section, payload.expected_updated_at)
        section.is_enabled = payload.is_enabled
        section.updated_at = _next_timestamp(section.updated_at)
        self._commit()
        return section

    def _reorder(
        self,
        invitation: Invitation,
        payload: SectionReorder,
    ) -> list[InvitationSection]:
        invitation = self._lock_invitation(invitation)
        self._ensure_editable(invitation)
        sections = self.sections.list_for_invitation(invitation.id)
        by_id = {section.id: section for section in sections}
        if set(payload.section_ids) != set(by_id) or len(payload.section_ids) != len(by_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Reorder must include every section belonging to this invitation exactly once",
            )
        now = datetime.now(timezone.utc)
        for sort_order, section_id in enumerate(payload.section_ids):
            section = by_id[section_id]
            section.sort_order = sort_order
            section.updated_at = _next_timestamp(section.updated_at, now)
        self._commit()
        return [by_id[section_id] for section_id in payload.section_ids]

    def _reset(self, invitation: Invitation, section_id: UUID) -> InvitationSection:
        invitation = self._lock_invitation(invitation)
        self._ensure_editable(invitation)
        section = self.sections.get_for_invitation(invitation.id, section_id, lock=True)
        self._check_section_and_version(section, None)
        template = self.session.get(Template, invitation.template_id)
        defaults = (
            template.configuration.get("default_sections", [])
            if template is not None and isinstance(template.configuration, dict)
            else []
        )
        if not isinstance(defaults, list):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template default sections are invalid",
            )
        try:
            default = next(
                (
                    DefaultSection.model_validate(item)
                    for item in defaults
                    if isinstance(item, dict) and item.get("key") == section.section_key
                ),
                None,
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template default sections are invalid",
            ) from error
        if default is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Section no longer exists in the source template",
            )
        section.section_type = default.type
        section.title = default.title
        section.content = default.configuration
        section.is_enabled = default.is_enabled
        section.updated_at = _next_timestamp(section.updated_at)
        self._commit()
        return section

    def _validate_media_references(
        self, invitation_id: UUID, content: dict[str, Any]
    ) -> None:
        media_ids: set[UUID] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if "media_id" in value:
                    try:
                        media_ids.add(UUID(str(value["media_id"])))
                    except (TypeError, ValueError) as error:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Section media_id values must be valid UUIDs",
                        ) from error
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(content)
        if self.media.find_ids_for_invitation(invitation_id, media_ids) != media_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Section media must belong to this invitation",
            )

    @staticmethod
    def _ensure_editable(invitation: Invitation) -> None:
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot be edited",
            )

    def _lock_invitation(self, invitation: Invitation) -> Invitation:
        if invitation.owner_id is not None:
            locked = self.invitations.find_personal(
                invitation.owner_id, invitation.id, lock=True
            )
        else:
            locked = self.invitations.find_workspace(
                invitation.workspace_id, invitation.id, lock=True
            )
        if locked is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return locked

    @staticmethod
    def _check_section_and_version(
        section: InvitationSection | None,
        expected_updated_at: datetime | None,
    ) -> None:
        if section is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
        if (
            expected_updated_at is not None
            and _normalize_timestamp(section.updated_at) != _normalize_timestamp(expected_updated_at)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Section was changed by another request; reload before editing",
            )

    def _commit(self) -> None:
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_timestamp(
    current: datetime,
    now: datetime | None = None,
) -> datetime:
    current_utc = _normalize_timestamp(current)
    candidate = now or datetime.now(timezone.utc)
    candidate_utc = _normalize_timestamp(candidate)
    return max(candidate_utc, current_utc + timedelta(microseconds=1))
