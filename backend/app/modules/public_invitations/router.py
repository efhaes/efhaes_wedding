import secrets
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.rate_limit import rate_limit
from app.core.security import (
    bearer_token,
    sign_visitor_identifier,
    verify_visitor_identifier,
)
from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.models.invitation_media import InvitationMedia
from app.models.workspace import WorkspaceMember
from app.modules.public_invitations.schemas.guest_access import (
    GuestCreate,
    GuestLinkRead,
    GuestLinkRegenerated,
    GuestPage,
    GuestRead,
    GuestUpdate,
)
from app.modules.public_invitations.schemas.public_invitation import (
    InvitationPublishRead,
    InvitationViewCount,
    PublicInvitationResponse,
)
from app.modules.public_invitations.services.guest_access_service import GuestAccessService
from app.modules.public_invitations.services.invitation_view_service import (
    InvitationViewService,
)
from app.modules.public_invitations.services.public_invitation_service import (
    PublicInvitationService,
)
from app.modules.media.storage import MediaStorage, get_media_storage
from app.modules.users.models.user import User


router = APIRouter(tags=["public-invitations", "guest-access"])
public_router = APIRouter(
    prefix="/public/invitations",
    tags=["public-invitations"],
    dependencies=[Depends(rate_limit("public"))],
)
guest_router = APIRouter(
    prefix="/invitations",
    tags=["guest-access"],
    dependencies=[Depends(rate_limit("guest_access"))],
)

Database = Annotated[Session, Depends(get_db)]
AuthenticatedUser = Annotated[User, Depends(require_authenticated_user)]
Storage = Annotated[MediaStorage, Depends(get_media_storage)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
WorkspacePublisher = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin")),
]
WorkspaceGuestManager = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor")),
]
WorkspaceInvitationViewer = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor", "member")),
]
_VISITOR_COOKIE = "public_viewer"


def _management_service(session: Session) -> PublicInvitationService:
    return PublicInvitationService(session)


def _guest_service(session: Session) -> GuestAccessService:
    return GuestAccessService(session)


def _public_file_response(
    media: InvitationMedia,
    stream: BinaryIO,
) -> StreamingResponse:
    headers = {
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(media.original_filename, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return StreamingResponse(
        iter(lambda: stream.read(64 * 1024), b""),
        media_type=media.mime_type,
        headers=headers,
        background=BackgroundTask(stream.close),
    )


@public_router.get(
    "/{slug}",
    response_model=PublicInvitationResponse,
    summary="Get a published public invitation",
    description=(
        "No user login is required. An optional guest token may be sent as "
        "`Authorization: Bearer <guest-token>` to personalize the response. "
        "Guest tokens in query parameters are not part of this API contract."
    ),
    responses={
        404: {"description": "Invitation not found, unpublished, or expired"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def get_public_invitation(
    slug: str,
    request: Request,
    response: Response,
    session: Database,
    visitor_cookie: Annotated[str | None, Cookie(alias=_VISITOR_COOKIE)] = None,
) -> PublicInvitationResponse:
    visitor_key = verify_visitor_identifier(visitor_cookie)
    if visitor_key is None:
        visitor_key = secrets.token_urlsafe(32)
    guest_token = bearer_token(request.headers.get("authorization")) or getattr(
        request.state, "legacy_guest_token", None
    )
    result = _management_service(session).get_public(slug, guest_token, visitor_key)
    response.set_cookie(
        _VISITOR_COOKIE,
        sign_visitor_identifier(visitor_key),
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api/v1/public/invitations",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return result


@public_router.get(
    "/{slug}/media/{media_id}/file",
    summary="Get media referenced by an active public section",
    responses={
        200: {
            "description": "Image bytes",
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {"description": "Invitation or referenced media not found"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def get_public_invitation_media(
    slug: str,
    media_id: UUID,
    session: Database,
    storage: Storage,
) -> StreamingResponse:
    media = _management_service(session).public_media(slug, media_id)
    try:
        stream = storage.open(media.storage_key)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        ) from error
    return _public_file_response(media, stream)


@router.post(
    "/invitations/personal/{invitation_id}/publish",
    response_model=InvitationPublishRead,
    dependencies=[Depends(require_csrf)],
)
def publish_personal_invitation(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> InvitationPublishRead:
    return _management_service(session).publish_personal(user, invitation_id)


@router.post(
    "/invitations/personal/{invitation_id}/unpublish",
    response_model=InvitationPublishRead,
    dependencies=[Depends(require_csrf)],
)
def unpublish_personal_invitation(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> InvitationPublishRead:
    return _management_service(session).unpublish_personal(user, invitation_id)


@router.post(
    "/invitations/workspaces/{workspace_id}/{invitation_id}/publish",
    response_model=InvitationPublishRead,
    dependencies=[Depends(require_csrf)],
)
def publish_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    _membership: WorkspacePublisher,
) -> InvitationPublishRead:
    return _management_service(session).publish_workspace(
        workspace_id, invitation_id
    )


@router.post(
    "/invitations/workspaces/{workspace_id}/{invitation_id}/unpublish",
    response_model=InvitationPublishRead,
    dependencies=[Depends(require_csrf)],
)
def unpublish_workspace_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    _membership: WorkspacePublisher,
) -> InvitationPublishRead:
    return _management_service(session).unpublish_workspace(
        workspace_id, invitation_id
    )


@guest_router.post(
    "/personal/{invitation_id}/guests",
    response_model=GuestLinkRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_personal_guest(
    invitation_id: UUID,
    payload: GuestCreate,
    session: Database,
    user: AuthenticatedUser,
) -> GuestLinkRead:
    return _guest_service(session).create_personal(user, invitation_id, payload)


@guest_router.get(
    "/personal/{invitation_id}/guests",
    response_model=GuestPage,
)
def list_personal_guests(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> GuestPage:
    return _guest_service(session).list_personal(
        user, invitation_id, page, page_size
    )


@guest_router.get(
    "/personal/{invitation_id}/guests/{guest_id}",
    response_model=GuestRead,
)
def get_personal_guest(
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> GuestRead:
    return _guest_service(session).get_personal(user, invitation_id, guest_id)


@guest_router.patch(
    "/personal/{invitation_id}/guests/{guest_id}",
    response_model=GuestRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_guest(
    invitation_id: UUID,
    guest_id: UUID,
    payload: GuestUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> GuestRead:
    return _guest_service(session).update_personal(
        user, invitation_id, guest_id, payload
    )


@guest_router.delete(
    "/personal/{invitation_id}/guests/{guest_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_personal_guest(
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> Response:
    _guest_service(session).delete_personal(user, invitation_id, guest_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@guest_router.post(
    "/personal/{invitation_id}/guests/{guest_id}/regenerate-link",
    response_model=GuestLinkRegenerated,
    dependencies=[Depends(require_csrf)],
)
def regenerate_personal_guest_link(
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> GuestLinkRegenerated:
    return _guest_service(session).regenerate_personal(
        user, invitation_id, guest_id
    )


@guest_router.post(
    "/workspaces/{workspace_id}/{invitation_id}/guests",
    response_model=GuestLinkRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_workspace_guest(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: GuestCreate,
    session: Database,
    _membership: WorkspaceGuestManager,
) -> GuestLinkRead:
    return _guest_service(session).create_workspace(
        workspace_id, invitation_id, payload
    )


@guest_router.get(
    "/workspaces/{workspace_id}/{invitation_id}/guests",
    response_model=GuestPage,
)
def list_workspace_guests(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    _membership: WorkspaceInvitationViewer,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> GuestPage:
    return _guest_service(session).list_workspace(
        workspace_id, invitation_id, page, page_size
    )


@guest_router.get(
    "/workspaces/{workspace_id}/{invitation_id}/guests/{guest_id}",
    response_model=GuestRead,
)
def get_workspace_guest(
    workspace_id: UUID,
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    _membership: WorkspaceInvitationViewer,
) -> GuestRead:
    return _guest_service(session).get_workspace(
        workspace_id, invitation_id, guest_id
    )


@guest_router.patch(
    "/workspaces/{workspace_id}/{invitation_id}/guests/{guest_id}",
    response_model=GuestRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_guest(
    workspace_id: UUID,
    invitation_id: UUID,
    guest_id: UUID,
    payload: GuestUpdate,
    session: Database,
    _membership: WorkspaceGuestManager,
) -> GuestRead:
    return _guest_service(session).update_workspace(
        workspace_id, invitation_id, guest_id, payload
    )


@guest_router.delete(
    "/workspaces/{workspace_id}/{invitation_id}/guests/{guest_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_workspace_guest(
    workspace_id: UUID,
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    _membership: WorkspaceGuestManager,
) -> Response:
    _guest_service(session).delete_workspace(
        workspace_id, invitation_id, guest_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@guest_router.post(
    "/workspaces/{workspace_id}/{invitation_id}/guests/{guest_id}/regenerate-link",
    response_model=GuestLinkRegenerated,
    dependencies=[Depends(require_csrf)],
)
def regenerate_workspace_guest_link(
    workspace_id: UUID,
    invitation_id: UUID,
    guest_id: UUID,
    session: Database,
    _membership: WorkspaceGuestManager,
) -> GuestLinkRegenerated:
    return _guest_service(session).regenerate_workspace(
        workspace_id, invitation_id, guest_id
    )


@router.get(
    "/invitations/personal/{invitation_id}/views",
    response_model=InvitationViewCount,
)
def personal_invitation_views(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> InvitationViewCount:
    invitation = _management_service(session).invitations.find_personal(
        user.id, invitation_id
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    return InvitationViewCount(
        invitation_id=invitation.id,
        view_count=InvitationViewService(session).count(invitation.id),
    )


@router.get(
    "/invitations/workspaces/{workspace_id}/{invitation_id}/views",
    response_model=InvitationViewCount,
)
def workspace_invitation_views(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    _membership: WorkspaceInvitationViewer,
) -> InvitationViewCount:
    invitation = _management_service(session).invitations.find_workspace(
        workspace_id, invitation_id
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    return InvitationViewCount(
        invitation_id=invitation.id,
        view_count=InvitationViewService(session).count(invitation.id),
    )


router.include_router(public_router)
router.include_router(guest_router)
