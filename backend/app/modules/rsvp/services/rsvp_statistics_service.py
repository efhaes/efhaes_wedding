from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.invitations.repositories.invitation_repository import (
    InvitationRepository,
)
from app.modules.rsvp.repositories.rsvp_repository import RsvpRepository
from app.modules.rsvp.schemas.rsvp_statistics import RsvpStatistics
from app.modules.users.models.user import User
from fastapi import HTTPException, status


class RsvpStatisticsService:
    def __init__(self, session: Session) -> None:
        self.invitations = InvitationRepository(session)
        self.repository = RsvpRepository(session)

    def personal(self, user: User, invitation_id: UUID) -> RsvpStatistics:
        invitation = self.invitations.find_personal(user.id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return RsvpStatistics.model_validate(
            self.repository.statistics(invitation.id)
        )

    def workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> RsvpStatistics:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return RsvpStatistics.model_validate(
            self.repository.statistics(invitation.id)
        )
