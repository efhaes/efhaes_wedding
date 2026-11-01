from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.models.workspace import WorkspaceMember
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.schemas.invitation import (
    InvitationCreate,
    InvitationPage,
    InvitationRead,
    InvitationUpdate,
    PersonalInvitationCreate,
    WorkspaceInvitationCreate,
)
from app.modules.invitations.services.invitation_service import InvitationService
from app.modules.users.models.user import User


router = APIRouter(prefix="/invitations", tags=["invitations"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
StatusFilter = Annotated[
    Literal["draft", "published", "archived"] | None,
    Query(alias="status"),
]
SearchFilter = Annotated[str | None, Query(min_length=1, max_length=100)]
SortBy = Literal["created_at", "updated_at"]
SortOrder = Literal["asc", "desc"]

_VIEW_ROLES = ("owner", "admin", "editor", "member")
_EDIT_ROLES = ("owner", "admin", "editor")
_ARCHIVE_ROLES = ("owner", "admin")


@router.post(
    "/personal",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_personal_invitation(
    payload: PersonalInvitationCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> Invitation:
    return InvitationService(session).create_personal(user, payload)


@router.get("/personal", response_model=InvitationPage)
def list_personal_invitations(
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    search: SearchFilter = None,
    status_filter: StatusFilter = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: SortBy = "created_at",
    sort_order: SortOrder = "desc",
) -> InvitationPage:
    return InvitationService(session).list_personal(
        user,
        search=search,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/personal/{invitation_id}", response_model=InvitationRead)
def get_personal_invitation(
    invitation_id: UUID,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> Invitation:
    return InvitationService(session).get_personal(user, invitation_id)


@router.patch(
    "/personal/{invitation_id}",
    response_model=InvitationRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_invitation(
    invitation_id: UUID,
    payload: InvitationUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> Invitation:
    return InvitationService(session).update_personal(user, invitation_id, payload)


@router.post(
    "/personal/{invitation_id}/archive",
    response_model=InvitationRead,
    dependencies=[Depends(require_csrf)],
)
def archive_personal_invitation(
    invitation_id: UUID,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> Invitation:
    return InvitationService(session).archive_personal(user, invitation_id)


@router.post(
    "/workspaces/{workspace_id}",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_workspace_invitation(
    workspace_id: UUID,
    payload: WorkspaceInvitationCreate,
    session: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_workspace_role(*_EDIT_ROLES)),
) -> Invitation:
    return InvitationService(session).create_workspace(
        workspace_id,
        membership,
        payload,
        payload.client_id,
    )


@router.get("/workspaces/{workspace_id}", response_model=InvitationPage)
def list_workspace_invitations(
    workspace_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_VIEW_ROLES)),
    search: SearchFilter = None,
    status_filter: StatusFilter = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: SortBy = "created_at",
    sort_order: SortOrder = "desc",
) -> InvitationPage:
    return InvitationService(session).list_workspace(
        workspace_id,
        search=search,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}",
    response_model=InvitationRead,
)
def get_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_VIEW_ROLES)),
) -> Invitation:
    return InvitationService(session).get_workspace(workspace_id, invitation_id)


@router.patch(
    "/workspaces/{workspace_id}/{invitation_id}",
    response_model=InvitationRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: InvitationUpdate,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_EDIT_ROLES)),
) -> Invitation:
    return InvitationService(session).update_workspace(
        workspace_id,
        invitation_id,
        payload,
        _membership,
    )


@router.post(
    "/workspaces/{workspace_id}/{invitation_id}/archive",
    response_model=InvitationRead,
    dependencies=[Depends(require_csrf)],
)
def archive_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Session = Depends(get_db),
    _membership: WorkspaceMember = Depends(require_workspace_role(*_ARCHIVE_ROLES)),
) -> Invitation:
    return InvitationService(session).archive_workspace(workspace_id, invitation_id)
