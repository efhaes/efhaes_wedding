from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.users.models.user import User
from app.modules.workspaces.schemas.member import MemberCreate, MemberRead, MemberRoleUpdate
from app.modules.workspaces.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.modules.workspaces.services.member_service import MemberService
from app.modules.workspaces.services.workspace_service import WorkspaceService


router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_ACTIVE_MEMBER_ROLES = ("owner", "admin", "editor", "member")
_MANAGER_ROLES = ("owner", "admin")


@router.post(
    "/",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_workspace(
    payload: WorkspaceCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> Workspace:
    return WorkspaceService(session).create(user, payload)


@router.get("/", response_model=list[WorkspaceRead])
def list_workspaces(
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> list[Workspace]:
    return WorkspaceService(session).list_for_user(user)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(
    workspace_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_ACTIVE_MEMBER_ROLES)),
) -> Workspace:
    return WorkspaceService(session).get_for_member(workspace_id)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_MANAGER_ROLES)),
) -> Workspace:
    service = WorkspaceService(session)
    workspace = service.get_for_member(workspace_id)
    return service.update(workspace, payload)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def deactivate_workspace(
    workspace_id: UUID,
    session: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_workspace_role("owner")),
) -> Response:
    service = WorkspaceService(session)
    workspace = service.get_for_member(workspace_id)
    if membership.user_id != workspace.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can deactivate it",
        )
    service.deactivate(workspace)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}/members", response_model=list[MemberRead])
def list_members(
    workspace_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_ACTIVE_MEMBER_ROLES)),
) -> list[WorkspaceMember]:
    return MemberService(session).list_members(workspace_id)


@router.post(
    "/{workspace_id}/members",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def add_member(
    workspace_id: UUID,
    payload: MemberCreate,
    session: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_workspace_role(*_MANAGER_ROLES)),
) -> WorkspaceMember:
    workspace = WorkspaceService(session).get_for_member(workspace_id)
    return MemberService(session).add_member(workspace, membership, payload)


@router.get("/{workspace_id}/members/{member_id}", response_model=MemberRead)
def get_member(
    workspace_id: UUID,
    member_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_ACTIVE_MEMBER_ROLES)),
) -> WorkspaceMember:
    return MemberService(session).get_member(workspace_id, member_id)


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=MemberRead,
    dependencies=[Depends(require_csrf)],
)
def update_member_role(
    workspace_id: UUID,
    member_id: UUID,
    payload: MemberRoleUpdate,
    session: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_workspace_role(*_MANAGER_ROLES)),
) -> WorkspaceMember:
    workspace = WorkspaceService(session).get_for_member(workspace_id)
    return MemberService(session).update_member_role(
        workspace,
        membership,
        member_id,
        payload,
    )


@router.delete(
    "/{workspace_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_member(
    workspace_id: UUID,
    member_id: UUID,
    session: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_workspace_role(*_MANAGER_ROLES)),
) -> Response:
    workspace = WorkspaceService(session).get_for_member(workspace_id)
    MemberService(session).remove_member(workspace, membership, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
