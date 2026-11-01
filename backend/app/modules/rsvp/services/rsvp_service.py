from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import (
    InvitationRepository,
)
from app.modules.rsvp.repositories.rsvp_repository import RsvpRepository
from app.modules.rsvp.schemas.rsvp import (
    RsvpPage,
    RsvpRead,
    RsvpStatusFilter,
    RsvpUpdate,
    rsvp_read_data,
)
from app.modules.rsvp.services.rsvp_configuration import (
    commit_rsvp,
    database_status,
    effective_configuration,
)
from app.modules.users.models.user import User


RsvpSortBy = Literal["created_at", "updated_at", "responded_at"]
RsvpSortOrder = Literal["asc", "desc"]


class RsvpService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = RsvpRepository(session)
        self.invitations = InvitationRepository(session)

    def list_personal(
        self,
        user: User,
        invitation_id: UUID,
        *,
        status_filter: RsvpStatusFilter | None,
        page: int,
        page_size: int,
        sort_by: RsvpSortBy,
        sort_order: RsvpSortOrder,
    ) -> RsvpPage:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._page(
            invitation.id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def list_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        status_filter: RsvpStatusFilter | None,
        page: int,
        page_size: int,
        sort_by: RsvpSortBy,
        sort_order: RsvpSortOrder,
    ) -> RsvpPage:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._page(
            invitation.id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def get_personal(
        self,
        user: User,
        invitation_id: UUID,
        rsvp_id: UUID,
    ) -> RsvpRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._get(invitation.id, rsvp_id)

    def get_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        rsvp_id: UUID,
    ) -> RsvpRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._get(invitation.id, rsvp_id)

    def update_personal(
        self,
        user: User,
        invitation_id: UUID,
        rsvp_id: UUID,
        payload: RsvpUpdate,
    ) -> RsvpRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._update(invitation.id, rsvp_id, payload)

    def update_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        rsvp_id: UUID,
        payload: RsvpUpdate,
    ) -> RsvpRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._update(invitation.id, rsvp_id, payload)

    def delete_personal(
        self,
        user: User,
        invitation_id: UUID,
        rsvp_id: UUID,
    ) -> None:
        invitation = self._personal_invitation(user.id, invitation_id)
        self._delete(invitation.id, rsvp_id)

    def delete_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        rsvp_id: UUID,
    ) -> None:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        self._delete(invitation.id, rsvp_id)

    def _personal_invitation(self, owner_id: UUID, invitation_id: UUID) -> Invitation:
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

    def _page(
        self,
        invitation_id: UUID,
        *,
        status_filter: RsvpStatusFilter | None,
        page: int,
        page_size: int,
        sort_by: RsvpSortBy,
        sort_order: RsvpSortOrder,
    ) -> RsvpPage:
        rows, total = self.repository.list_for_invitation(
            invitation_id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return RsvpPage(
            items=[
                RsvpRead.model_validate(rsvp_read_data(rsvp, guest_name=guest_name))
                for rsvp, guest_name in rows
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _get(self, invitation_id: UUID, rsvp_id: UUID) -> RsvpRead:
        result = self.repository.get_for_invitation(invitation_id, rsvp_id)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RSVP not found",
            )
        rsvp, guest_name = result
        return RsvpRead.model_validate(
            rsvp_read_data(rsvp, guest_name=guest_name)
        )

    def _update(
        self,
        invitation_id: UUID,
        rsvp_id: UUID,
        payload: RsvpUpdate,
    ) -> RsvpRead:
        result = self.repository.get_for_invitation(
            invitation_id,
            rsvp_id,
            lock=True,
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RSVP not found",
            )
        rsvp, guest_name = result
        changes = payload.model_dump(exclude_unset=True)
        current_status = (
            "not_attending"
            if rsvp.attendance_status == "declined"
            else rsvp.attendance_status
        )
        next_status = changes.get("attendance_status", current_status)
        next_count = changes.get(
            "number_of_attendees",
            0 if next_status == "not_attending" else rsvp.guest_count,
        )
        if next_status == "attending" and next_count < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Attending guests must have at least one attendee",
            )
        if next_status == "not_attending" and next_count != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Non-attending guests must have zero attendees",
            )
        section = self.repository.get_rsvp_section(invitation_id)
        configuration = effective_configuration(section)
        if (
            next_status == "attending"
            and next_count > configuration.max_attendees_per_guest
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Attendee count exceeds this invitation's limit",
            )
        rsvp.attendance_status = database_status(next_status)
        rsvp.guest_count = next_count
        if "message" in changes:
            rsvp.message = changes["message"]
        rsvp.responded_at = datetime.now(timezone.utc)
        commit_rsvp(self.session)
        return RsvpRead.model_validate(
            rsvp_read_data(rsvp, guest_name=guest_name)
        )

    def _delete(self, invitation_id: UUID, rsvp_id: UUID) -> None:
        result = self.repository.get_for_invitation(
            invitation_id,
            rsvp_id,
            lock=True,
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RSVP not found",
            )
        self.session.delete(result[0])
        commit_rsvp(self.session)
