import re
import unicodedata
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceMember
from app.modules.users.models.user import User
from app.modules.workspaces.repositories.workspace_repository import WorkspaceRepository
from app.modules.workspaces.schemas.workspace import WorkspaceCreate, WorkspaceUpdate


class WorkspaceService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = WorkspaceRepository(session)

    def create(self, owner: User, payload: WorkspaceCreate) -> Workspace:
        slug = self._make_slug(payload.name)
        workspace = Workspace(
            name=payload.name,
            slug=slug,
            owner_id=owner.id,
            status="active",
        )
        owner_membership = WorkspaceMember(
            workspace=workspace,
            user_id=owner.id,
            role="owner",
            status="active",
            joined_at=datetime.now(timezone.utc),
        )
        self.session.add_all([workspace, owner_membership])
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace could not be created because of a data conflict",
            ) from error
        return workspace

    def list_for_user(self, user: User) -> list[Workspace]:
        return self.repository.find_active_for_user(user.id)

    def get_for_member(self, workspace_id: UUID) -> Workspace:
        workspace = self.repository.find_active_by_id(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        return workspace

    def update(self, workspace: Workspace, payload: WorkspaceUpdate) -> Workspace:
        workspace.name = payload.name
        self.session.commit()
        return workspace

    def deactivate(self, workspace: Workspace) -> None:
        workspace.status = "archived"
        self.session.commit()

    @staticmethod
    def _make_slug(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
        base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "workspace"
        suffix = uuid4().hex[:12]
        return f"{base[:146].rstrip('-')}-{suffix}"
