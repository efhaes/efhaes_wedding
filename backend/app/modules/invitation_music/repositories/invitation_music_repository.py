from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.invitation_music.models.invitation_music import InvitationMusic


class InvitationMusicRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_invitation(
        self,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> InvitationMusic | None:
        statement = select(InvitationMusic).where(
            InvitationMusic.invitation_id == invitation_id
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

