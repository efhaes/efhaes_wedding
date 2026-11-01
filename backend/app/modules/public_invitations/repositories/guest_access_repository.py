from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.guest import Guest


class GuestAccessRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_invitation(
        self,
        invitation_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Guest], int]:
        statement = select(Guest).where(Guest.invitation_id == invitation_id)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.subquery())
            )
            or 0
        )
        guests = list(
            self.session.scalars(
                statement.order_by(Guest.created_at.desc(), Guest.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return guests, total

    def get_for_invitation(
        self,
        invitation_id: UUID,
        guest_id: UUID,
        *,
        lock: bool = False,
    ) -> Guest | None:
        statement = select(Guest).where(
            Guest.invitation_id == invitation_id,
            Guest.id == guest_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

