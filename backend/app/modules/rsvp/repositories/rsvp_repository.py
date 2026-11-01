from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.invitation_section import InvitationSection
from app.models.rsvp import RSVP
from app.modules.invitations.models.invitation import Invitation


class RsvpRepository:
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

    def get_rsvp_section(
        self,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> InvitationSection | None:
        statement = select(InvitationSection).where(
            InvitationSection.invitation_id == invitation_id,
            InvitationSection.section_key == "rsvp",
            InvitationSection.section_type == "rsvp",
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_for_guest(
        self,
        invitation_id: UUID,
        guest_id: UUID,
        *,
        lock: bool = False,
    ) -> RSVP | None:
        statement = select(RSVP).where(
            RSVP.invitation_id == invitation_id,
            RSVP.guest_id == guest_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_for_invitation(
        self,
        invitation_id: UUID,
        rsvp_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[RSVP, str | None] | None:
        statement = (
            select(RSVP, Guest.name)
            .outerjoin(
                Guest,
                (Guest.id == RSVP.guest_id)
                & (Guest.invitation_id == RSVP.invitation_id),
            )
            .where(
                RSVP.invitation_id == invitation_id,
                RSVP.id == rsvp_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        row = self.session.execute(statement).first()
        return (row[0], row[1]) if row is not None else None

    def list_for_invitation(
        self,
        invitation_id: UUID,
        *,
        status_filter: Literal["attending", "not_attending", "pending"] | None,
        page: int,
        page_size: int,
        sort_by: Literal["created_at", "updated_at", "responded_at"],
        sort_order: Literal["asc", "desc"],
    ) -> tuple[list[tuple[RSVP, str | None]], int]:
        statement = (
            select(RSVP, Guest.name)
            .outerjoin(
                Guest,
                (Guest.id == RSVP.guest_id)
                & (Guest.invitation_id == RSVP.invitation_id),
            )
            .where(RSVP.invitation_id == invitation_id)
        )
        if status_filter == "not_attending":
            statement = statement.where(RSVP.attendance_status == "declined")
        elif status_filter in ("attending", "pending"):
            statement = statement.where(RSVP.attendance_status == status_filter)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        sort_column = {
            "created_at": RSVP.created_at,
            "updated_at": RSVP.updated_at,
            "responded_at": RSVP.responded_at,
        }[sort_by]
        order_clause = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        rows = list(
            self.session.execute(
                statement.order_by(order_clause, RSVP.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return [(row[0], row[1]) for row in rows], total

    def statistics(self, invitation_id: UUID) -> dict[str, int]:
        statement = (
            select(
                func.count(Guest.id),
                func.count(
                    case(
                        (
                            RSVP.attendance_status.in_(("attending", "declined")),
                            RSVP.id,
                        )
                    )
                ),
                func.count(
                    case((RSVP.attendance_status == "attending", RSVP.id))
                ),
                func.count(
                    case((RSVP.attendance_status == "declined", RSVP.id))
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (RSVP.attendance_status == "attending", RSVP.guest_count),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .select_from(Guest)
            .outerjoin(
                RSVP,
                (RSVP.invitation_id == Guest.invitation_id)
                & (RSVP.guest_id == Guest.id),
            )
            .where(Guest.invitation_id == invitation_id)
        )
        (
            total_guests,
            responded_guests,
            attending_guests,
            not_attending_guests,
            total_attending_people,
        ) = self.session.execute(statement).one()
        return {
            "total_guests": int(total_guests or 0),
            "responded_guests": int(responded_guests or 0),
            "not_responded_guests": int((total_guests or 0) - (responded_guests or 0)),
            "attending_guests": int(attending_guests or 0),
            "not_attending_guests": int(not_attending_guests or 0),
            "total_attending_people": int(total_attending_people or 0),
        }
