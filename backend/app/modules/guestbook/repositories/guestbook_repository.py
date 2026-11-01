from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.guestbook import GuestbookEntry
from app.models.invitation_section import InvitationSection
from app.modules.invitations.models.invitation import Invitation


class GuestbookRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_public_invitation(
        self,
        slug: str,
        *,
        lock: bool = False,
    ) -> Invitation | None:
        statement = select(Invitation).where(
            Invitation.slug == slug,
            Invitation.status == "published",
            or_(Invitation.expires_at.is_(None), Invitation.expires_at > func.now()),
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_section(
        self,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> InvitationSection | None:
        statement = select(InvitationSection).where(
            InvitationSection.invitation_id == invitation_id,
            InvitationSection.section_key == "guestbook",
            InvitationSection.section_type == "guestbook",
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def find_guest_by_token(
        self,
        invitation_id: UUID,
        token_hash: str,
        *,
        lock: bool = False,
    ) -> Guest | None:
        statement = select(Guest).where(
            Guest.invitation_id == invitation_id,
            Guest.invitation_token == token_hash,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def add(self, entry: GuestbookEntry) -> GuestbookEntry:
        self.session.add(entry)
        return entry

    def get_entry(
        self,
        invitation_id: UUID,
        entry_id: UUID,
        *,
        lock: bool = False,
    ) -> GuestbookEntry | None:
        statement = select(GuestbookEntry).where(
            GuestbookEntry.invitation_id == invitation_id,
            GuestbookEntry.id == entry_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_for_invitation(
        self,
        invitation_id: UUID,
        *,
        status_filter: Literal["pending", "approved", "hidden"] | None,
        page: int,
        page_size: int,
        sort_order: Literal["asc", "desc"],
    ) -> tuple[list[GuestbookEntry], int]:
        statement = select(GuestbookEntry).where(
            GuestbookEntry.invitation_id == invitation_id
        )
        if status_filter is not None:
            statement = statement.where(GuestbookEntry.status == status_filter)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        created_order = (
            GuestbookEntry.created_at.asc()
            if sort_order == "asc"
            else GuestbookEntry.created_at.desc()
        )
        entries = list(
            self.session.scalars(
                statement.order_by(created_order, GuestbookEntry.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return entries, total

    def public_page(
        self,
        invitation_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[GuestbookEntry], int]:
        statement = select(GuestbookEntry).where(
            GuestbookEntry.invitation_id == invitation_id,
            GuestbookEntry.status == "approved",
        )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        entries = list(
            self.session.scalars(
                statement.order_by(
                    GuestbookEntry.created_at.desc(),
                    GuestbookEntry.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return entries, total

    def statistics(self, invitation_id: UUID) -> dict[str, int]:
        total, pending, approved, hidden = self.session.execute(
            select(
                func.count(GuestbookEntry.id),
                func.count(
                    case((GuestbookEntry.status == "pending", GuestbookEntry.id))
                ),
                func.count(
                    case((GuestbookEntry.status == "approved", GuestbookEntry.id))
                ),
                func.count(
                    case((GuestbookEntry.status == "hidden", GuestbookEntry.id))
                ),
            ).where(GuestbookEntry.invitation_id == invitation_id)
        ).one()
        return {
            "total_messages": int(total or 0),
            "pending_messages": int(pending or 0),
            "approved_messages": int(approved or 0),
            "hidden_messages": int(hidden or 0),
        }
