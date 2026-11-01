from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.core.rate_limit import rate_limit
from app.core.security import bearer_token
from app.models.workspace import WorkspaceMember
from app.modules.rsvp.schemas.rsvp import (
    PublicRsvpSubmission,
    PublicRsvpRead,
    RsvpPage,
    RsvpRead,
    RsvpSettingsRead,
    RsvpSettingsUpdate,
    RsvpStatusFilter,
    RsvpUpdate,
)
from app.modules.rsvp.schemas.rsvp_statistics import RsvpStatistics
from app.modules.rsvp.services.rsvp_service import RsvpService
from app.modules.rsvp.services.public_rsvp_service import PublicRsvpService
from app.modules.rsvp.services.rsvp_settings_service import RsvpSettingsService
from app.modules.rsvp.services.rsvp_statistics_service import (
    RsvpStatisticsService,
)
from app.modules.users.models.user import User


router = APIRouter(tags=["rsvp"])
Database: TypeAlias = Annotated[Session, Depends(get_db)]
AuthenticatedUser: TypeAlias = Annotated[User, Depends(require_authenticated_user)]
WorkspaceRsvpViewer: TypeAlias = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor", "member")),
]
WorkspaceRsvpManager: TypeAlias = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin")),
]
WorkspaceRsvpSettingsEditor: TypeAlias = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor")),
]
PageNumber: TypeAlias = Annotated[int, Query(ge=1)]
PageSize: TypeAlias = Annotated[int, Query(ge=1, le=100)]
RsvpStatusQuery: TypeAlias = Annotated[
    RsvpStatusFilter | None,
    Query(alias="status"),
]
SortBy = Literal["created_at", "updated_at", "responded_at"]
SortOrder = Literal["asc", "desc"]


def _verify_workspace_scope(
    workspace_id: UUID,
    membership: WorkspaceMember,
) -> None:
    if membership.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )


@router.post(
    "/api/v1/public/invitations/{slug}/rsvp",
    response_model=PublicRsvpRead,
    dependencies=[Depends(rate_limit("submission"))],
    summary="Submit an RSVP for a public invitation",
    description=(
        "No user login is required. Supply the guest access token as "
        "`Authorization: Bearer <guest-token>`. The legacy request-body token "
        "field remains accepted for existing clients; query-string tokens are "
        "not part of this API contract."
    ),
    responses={
        401: {"description": "Guest token required"},
        404: {"description": "Invitation or guest token not found"},
        409: {"description": "RSVP is disabled or cannot be changed"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def submit_public_rsvp(
    slug: str,
    payload: PublicRsvpSubmission,
    session: Database,
    request: Request,
) -> PublicRsvpRead:
    token = bearer_token(request.headers.get("authorization"))
    if token is not None:
        payload = payload.model_copy(update={"guest_token": token})
    return PublicRsvpService(session).submit(slug, payload)


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/statistics",
    response_model=RsvpStatistics,
)
def get_personal_rsvp_statistics(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> RsvpStatistics:
    return RsvpStatisticsService(session).personal(user, invitation_id)


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/statistics",
    response_model=RsvpStatistics,
)
def get_workspace_rsvp_statistics(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceRsvpViewer,
) -> RsvpStatistics:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpStatisticsService(session).workspace(workspace_id, invitation_id)


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/settings",
    response_model=RsvpSettingsRead,
)
def get_personal_rsvp_settings(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> RsvpSettingsRead:
    return RsvpSettingsService(session).get_personal(user, invitation_id)


@router.patch(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/settings",
    response_model=RsvpSettingsRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_rsvp_settings(
    invitation_id: UUID,
    payload: RsvpSettingsUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> RsvpSettingsRead:
    return RsvpSettingsService(session).update_personal(user, invitation_id, payload)


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/settings",
    response_model=RsvpSettingsRead,
)
def get_workspace_rsvp_settings(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceRsvpViewer,
) -> RsvpSettingsRead:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpSettingsService(session).get_workspace(workspace_id, invitation_id)


@router.patch(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/settings",
    response_model=RsvpSettingsRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_rsvp_settings(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: RsvpSettingsUpdate,
    session: Database,
    membership: WorkspaceRsvpSettingsEditor,
) -> RsvpSettingsRead:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpSettingsService(session).update_workspace(
        workspace_id, invitation_id, payload
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/rsvps",
    response_model=RsvpPage,
)
def list_personal_rsvps(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
    status_filter: RsvpStatusQuery = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: SortBy = "created_at",
    sort_order: SortOrder = "desc",
) -> RsvpPage:
    return RsvpService(session).list_personal(
        user,
        invitation_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps",
    response_model=RsvpPage,
)
def list_workspace_rsvps(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceRsvpViewer,
    status_filter: RsvpStatusQuery = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: SortBy = "created_at",
    sort_order: SortOrder = "desc",
) -> RsvpPage:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpService(session).list_workspace(
        workspace_id,
        invitation_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/{rsvp_id}",
    response_model=RsvpRead,
)
def get_personal_rsvp(
    invitation_id: UUID,
    rsvp_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> RsvpRead:
    return RsvpService(session).get_personal(user, invitation_id, rsvp_id)


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/{rsvp_id}",
    response_model=RsvpRead,
)
def get_workspace_rsvp(
    workspace_id: UUID,
    invitation_id: UUID,
    rsvp_id: UUID,
    session: Database,
    membership: WorkspaceRsvpViewer,
) -> RsvpRead:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpService(session).get_workspace(workspace_id, invitation_id, rsvp_id)


@router.patch(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/{rsvp_id}",
    response_model=RsvpRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_rsvp(
    invitation_id: UUID,
    rsvp_id: UUID,
    payload: RsvpUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> RsvpRead:
    return RsvpService(session).update_personal(
        user, invitation_id, rsvp_id, payload
    )


@router.patch(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/{rsvp_id}",
    response_model=RsvpRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_rsvp(
    workspace_id: UUID,
    invitation_id: UUID,
    rsvp_id: UUID,
    payload: RsvpUpdate,
    session: Database,
    membership: WorkspaceRsvpManager,
) -> RsvpRead:
    _verify_workspace_scope(workspace_id, membership)
    return RsvpService(session).update_workspace(
        workspace_id, invitation_id, rsvp_id, payload
    )


@router.delete(
    "/api/v1/invitations/personal/{invitation_id}/rsvps/{rsvp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_personal_rsvp(
    invitation_id: UUID,
    rsvp_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> Response:
    RsvpService(session).delete_personal(user, invitation_id, rsvp_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/rsvps/{rsvp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_workspace_rsvp(
    workspace_id: UUID,
    invitation_id: UUID,
    rsvp_id: UUID,
    session: Database,
    membership: WorkspaceRsvpManager,
) -> Response:
    _verify_workspace_scope(workspace_id, membership)
    RsvpService(session).delete_workspace(workspace_id, invitation_id, rsvp_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
