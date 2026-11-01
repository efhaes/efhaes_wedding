from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.workspace import WorkspaceMember
from app.modules.users.models.user import User


class MemberRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_workspace(self, workspace_id: UUID) -> list[WorkspaceMember]:
        return list(
            self.session.scalars(
                select(WorkspaceMember)
                .options(joinedload(WorkspaceMember.user))
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.status == "active",
                )
                .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
            )
        )

    def find_by_id(
        self,
        workspace_id: UUID,
        member_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMember | None:
        statement = (
            select(WorkspaceMember)
            .options(joinedload(WorkspaceMember.user))
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.id == member_id,
                WorkspaceMember.status == "active",
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def find_for_user(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMember | None:
        return self.session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )

    def find_user_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def add(self, member: WorkspaceMember) -> WorkspaceMember:
        self.session.add(member)
        return member
