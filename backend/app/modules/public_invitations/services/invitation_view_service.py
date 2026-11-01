from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.public_invitations.repositories.public_invitation_repository import (
    PublicInvitationRepository,
)


class InvitationViewService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = PublicInvitationRepository(session)

    def count(self, invitation_id: UUID) -> int:
        return self.repository.view_count(invitation_id)
