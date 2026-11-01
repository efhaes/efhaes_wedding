from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invitation_media import InvitationMedia
from app.models.invitation_section import InvitationSection


class MediaRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_invitation(self, invitation_id: UUID) -> list[InvitationMedia]:
        return list(
            self.session.scalars(
                select(InvitationMedia)
                .where(InvitationMedia.invitation_id == invitation_id)
                .order_by(InvitationMedia.created_at, InvitationMedia.id)
            )
        )

    def get_for_invitation(
        self,
        invitation_id: UUID,
        media_id: UUID,
        *,
        lock: bool = False,
    ) -> InvitationMedia | None:
        statement = select(InvitationMedia).where(
            InvitationMedia.invitation_id == invitation_id,
            InvitationMedia.id == media_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def find_ids_for_invitation(
        self,
        invitation_id: UUID,
        media_ids: set[UUID],
    ) -> set[UUID]:
        if not media_ids:
            return set()
        rows = self.session.scalars(
            select(InvitationMedia.id).where(
                InvitationMedia.invitation_id == invitation_id,
                InvitationMedia.id.in_(media_ids),
            )
        )
        return set(rows)

    def has_section_reference(self, invitation_id: UUID, media_id: UUID) -> bool:
        sections = self.session.scalars(
            select(InvitationSection.content).where(
                InvitationSection.invitation_id == invitation_id
            )
        )
        return any(_contains_media_id(content, str(media_id)) for content in sections)


def _contains_media_id(value: object, media_id: str) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "media_id" and str(child).lower() == media_id.lower():
                return True
            if _contains_media_id(child, media_id):
                return True
    elif isinstance(value, list):
        return any(_contains_media_id(child, media_id) for child in value)
    return False
