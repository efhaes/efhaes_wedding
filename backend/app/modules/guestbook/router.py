from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.security import bearer_token
from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.models.workspace import WorkspaceMember
from app.modules.guestbook.schemas.guestbook import (
    GuestbookEntryRead,
    GuestbookModerationUpdate,
    GuestbookPage,
    GuestbookSettingsRead,
    GuestbookSettingsUpdate,
    GuestbookStatus,
    PublicGuestbookEntryRead,
    PublicGuestbookPage,
    PublicGuestbookSubmission,
)
from app.modules.guestbook.schemas.guestbook_statistics import GuestbookStatistics
from app.modules.guestbook.services.guestbook_moderation_service import (
    GuestbookModerationService,
)
from app.modules.guestbook.services.guestbook_service import GuestbookService
from app.modules.guestbook.services.public_guestbook_service import (
    PublicGuestbookService,
)
from app.modules.users.models.user import User


router = APIRouter(tags=["guestbook"])
Database: TypeAlias = Annotated[Session, Depends(get_db)]
AuthenticatedUser: TypeAlias = Annotated[User, Depends(require_authenticated_user)]
WorkspaceGuestbookViewer: TypeAlias = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor", "member")),
]
WorkspaceGuestbookManager: TypeAlias = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor")),
]
PageNumber: TypeAlias = Annotated[int, Query(ge=1)]
PageSize: TypeAlias = Annotated[int, Query(ge=1, le=100)]
PublicPageSize: TypeAlias = Annotated[int, Query(ge=1, le=50)]
StatusFilter: TypeAlias = Annotated[
    GuestbookStatus | None,
    Query(alias="status"),
]
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
    "/api/v1/public/invitations/{slug}/guestbook",
    response_model=PublicGuestbookEntryRead,
    dependencies=[Depends(rate_limit("submission"))],
    summary="Submit a guestbook message for a public invitation",
    description=(
        "No user login is required. Supply personalized guest access as "
        "`Authorization: Bearer <guest-token>`. The legacy request-body token "
        "field remains accepted for existing clients; query-string tokens are "
        "not part of this API contract."
    ),
    responses={
        403: {"description": "Personalized guest access is required"},
        404: {"description": "Invitation or guest token not found"},
        409: {"description": "Guestbook is unavailable"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def submit_public_guestbook(
    slug: str,
    payload: PublicGuestbookSubmission,
    session: Database,
    request: Request,
) -> PublicGuestbookEntryRead:
    token = bearer_token(request.headers.get("authorization"))
    if token is not None:
        payload = payload.model_copy(
            update={"guest_token": token, "guest_name": None}
        )
    return PublicGuestbookService(session).submit(slug, payload)


@router.get(
    "/api/v1/public/invitations/{slug}/guestbook",
    response_model=PublicGuestbookPage,
    dependencies=[Depends(rate_limit("public"))],
    summary="List approved guestbook messages for a public invitation",
    description="No user login or guest token is required.",
    responses={
        404: {"description": "Invitation or guestbook not found"},
        409: {"description": "Guestbook is unavailable"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def list_public_guestbook(
    slug: str,
    session: Database,
    page: PageNumber = 1,
    page_size: PublicPageSize = 10,
) -> PublicGuestbookPage:
    return PublicGuestbookService(session).public_page(
        slug,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/statistics",
    response_model=GuestbookStatistics,
)
def get_personal_guestbook_statistics(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> GuestbookStatistics:
    return GuestbookService(session).statistics_personal(user, invitation_id)


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/statistics",
    response_model=GuestbookStatistics,
)
def get_workspace_guestbook_statistics(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceGuestbookViewer,
) -> GuestbookStatistics:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookService(session).statistics_workspace(
        workspace_id,
        invitation_id,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/settings",
    response_model=GuestbookSettingsRead,
)
def get_personal_guestbook_settings(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> GuestbookSettingsRead:
    return GuestbookModerationService(session).get_settings_personal(
        user,
        invitation_id,
    )


@router.patch(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/settings",
    response_model=GuestbookSettingsRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_guestbook_settings(
    invitation_id: UUID,
    payload: GuestbookSettingsUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> GuestbookSettingsRead:
    return GuestbookModerationService(session).update_settings_personal(
        user,
        invitation_id,
        payload,
    )


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/settings",
    response_model=GuestbookSettingsRead,
)
def get_workspace_guestbook_settings(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceGuestbookViewer,
) -> GuestbookSettingsRead:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookModerationService(session).get_settings_workspace(
        workspace_id,
        invitation_id,
    )


@router.patch(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/settings",
    response_model=GuestbookSettingsRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_guestbook_settings(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: GuestbookSettingsUpdate,
    session: Database,
    membership: WorkspaceGuestbookManager,
) -> GuestbookSettingsRead:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookModerationService(session).update_settings_workspace(
        workspace_id,
        invitation_id,
        payload,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/guestbook",
    response_model=GuestbookPage,
)
def list_personal_guestbook(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
    status_filter: StatusFilter = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_order: SortOrder = "desc",
) -> GuestbookPage:
    return GuestbookService(session).list_personal(
        user,
        invitation_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_order=sort_order,
    )


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook",
    response_model=GuestbookPage,
)
def list_workspace_guestbook(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    membership: WorkspaceGuestbookViewer,
    status_filter: StatusFilter = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_order: SortOrder = "desc",
) -> GuestbookPage:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookService(session).list_workspace(
        workspace_id,
        invitation_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        sort_order=sort_order,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/{entry_id}",
    response_model=GuestbookEntryRead,
)
def get_personal_guestbook_entry(
    invitation_id: UUID,
    entry_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> GuestbookEntryRead:
    return GuestbookService(session).get_personal(user, invitation_id, entry_id)


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/{entry_id}",
    response_model=GuestbookEntryRead,
)
def get_workspace_guestbook_entry(
    workspace_id: UUID,
    invitation_id: UUID,
    entry_id: UUID,
    session: Database,
    membership: WorkspaceGuestbookViewer,
) -> GuestbookEntryRead:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookService(session).get_workspace(
        workspace_id,
        invitation_id,
        entry_id,
    )


@router.patch(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/{entry_id}/moderation",
    response_model=GuestbookEntryRead,
    dependencies=[Depends(require_csrf)],
)
def moderate_personal_guestbook_entry(
    invitation_id: UUID,
    entry_id: UUID,
    payload: GuestbookModerationUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> GuestbookEntryRead:
    return GuestbookModerationService(session).moderate_personal(
        user,
        invitation_id,
        entry_id,
        payload.status,
    )


@router.patch(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/{entry_id}/moderation",
    response_model=GuestbookEntryRead,
    dependencies=[Depends(require_csrf)],
)
def moderate_workspace_guestbook_entry(
    workspace_id: UUID,
    invitation_id: UUID,
    entry_id: UUID,
    payload: GuestbookModerationUpdate,
    session: Database,
    user: AuthenticatedUser,
    membership: WorkspaceGuestbookManager,
) -> GuestbookEntryRead:
    _verify_workspace_scope(workspace_id, membership)
    return GuestbookModerationService(session).moderate_workspace(
        workspace_id,
        invitation_id,
        entry_id,
        payload.status,
        user,
    )


@router.delete(
    "/api/v1/invitations/personal/{invitation_id}/guestbook/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_personal_guestbook_entry(
    invitation_id: UUID,
    entry_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> Response:
    GuestbookModerationService(session).delete_personal(
        user,
        invitation_id,
        entry_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/guestbook/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_workspace_guestbook_entry(
    workspace_id: UUID,
    invitation_id: UUID,
    entry_id: UUID,
    session: Database,
    membership: WorkspaceGuestbookManager,
) -> Response:
    _verify_workspace_scope(workspace_id, membership)
    GuestbookModerationService(session).delete_workspace(
        workspace_id,
        invitation_id,
        entry_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
