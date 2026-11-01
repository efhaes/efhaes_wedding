from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invitation_section import InvitationSection


class SectionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_invitation(self, invitation_id: UUID) -> list[InvitationSection]:
        return list(
            self.session.scalars(
                select(InvitationSection)
                .where(InvitationSection.invitation_id == invitation_id)
                .order_by(InvitationSection.sort_order, InvitationSection.id)
            )
        )

    def get_for_invitation(
        self,
        invitation_id: UUID,
        section_id: UUID,
        *,
        lock: bool = False,
    ) -> InvitationSection | None:
        statement = select(InvitationSection).where(
            InvitationSection.invitation_id == invitation_id,
            InvitationSection.id == section_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

