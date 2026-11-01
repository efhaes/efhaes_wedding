from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.guestbook import GuestbookEntry
from app.modules.guestbook.repositories.guestbook_repository import (
    GuestbookRepository,
)
from app.modules.guestbook.schemas.guestbook import (
    GuestbookEntryRead,
    GuestbookPage,
    GuestbookStatus,
    guestbook_read_data,
)
from app.modules.guestbook.schemas.guestbook_statistics import GuestbookStatistics
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import (
    InvitationRepository,
)
from app.modules.users.models.user import User


SortOrder = Literal["asc", "desc"]


class GuestbookService:
    def __init__(self, session: Session) -> None:
        self.repository = GuestbookRepository(session)
        self.invitations = InvitationRepository(session)

    def list_personal(
        self,
        user: User,
        invitation_id: UUID,
        *,
        status_filter: GuestbookStatus | None,
        page: int,
        page_size: int,
        sort_order: SortOrder,
    ) -> GuestbookPage:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._page(
            invitation.id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_order=sort_order,
        )

    def list_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        status_filter: GuestbookStatus | None,
        page: int,
        page_size: int,
        sort_order: SortOrder,
    ) -> GuestbookPage:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._page(
            invitation.id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_order=sort_order,
        )

    def get_personal(
        self,
        user: User,
        invitation_id: UUID,
        entry_id: UUID,
    ) -> GuestbookEntryRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._get(invitation.id, entry_id)

    def get_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        entry_id: UUID,
    ) -> GuestbookEntryRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._get(invitation.id, entry_id)

    def statistics_personal(
        self,
        user: User,
        invitation_id: UUID,
    ) -> GuestbookStatistics:
        invitation = self._personal_invitation(user.id, invitation_id)
        return GuestbookStatistics.model_validate(
            self.repository.statistics(invitation.id)
        )

    def statistics_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> GuestbookStatistics:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return GuestbookStatistics.model_validate(
            self.repository.statistics(invitation.id)
        )

    def _page(
        self,
        invitation_id: UUID,
        *,
        status_filter: GuestbookStatus | None,
        page: int,
        page_size: int,
        sort_order: SortOrder,
    ) -> GuestbookPage:
        entries, total = self.repository.list_for_invitation(
            invitation_id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_order=sort_order,
        )
        return GuestbookPage(
            items=[
                GuestbookEntryRead.model_validate(guestbook_read_data(entry))
                for entry in entries
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _get(
        self,
        invitation_id: UUID,
        entry_id: UUID,
    ) -> GuestbookEntryRead:
        entry = self.repository.get_entry(invitation_id, entry_id)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guestbook entry not found",
            )
        return GuestbookEntryRead.model_validate(guestbook_read_data(entry))

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
