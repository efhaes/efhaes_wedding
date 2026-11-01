from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceMember


class WorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, workspace: Workspace) -> Workspace:
        self.session.add(workspace)
        return workspace

    def find_active_by_id(self, workspace_id: UUID) -> Workspace | None:
        return self.session.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.status == "active",
            )
        )

    def find_active_for_user(self, user_id: UUID) -> list[Workspace]:
        return list(
            self.session.scalars(
                select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(
                    WorkspaceMember.user_id == user_id,
                    WorkspaceMember.status == "active",
                    Workspace.status == "active",
                )
                .order_by(Workspace.created_at.desc())
            )
        )

    def find_slug(self, slug: str) -> Workspace | None:
        return self.session.scalar(select(Workspace).where(Workspace.slug == slug))
