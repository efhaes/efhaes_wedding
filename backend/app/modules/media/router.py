from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.core.rate_limit import rate_limit
from app.models.invitation_media import InvitationMedia
from app.models.workspace import WorkspaceMember
from app.modules.media.schemas.media import MediaRead
from app.modules.media.services.media_service import MediaService
from app.modules.media.storage import MediaStorage, get_media_storage
from app.modules.users.models.user import User


router = APIRouter(prefix="/invitations", tags=["invitation-media"])
_VIEW_ROLES = ("owner", "admin", "editor", "member")
_EDIT_ROLES = ("owner", "admin", "editor")

Database = Annotated[Session, Depends(get_db)]
Storage = Annotated[MediaStorage, Depends(get_media_storage)]
AuthenticatedUser = Annotated[User, Depends(require_authenticated_user)]
WorkspaceEditor = Annotated[WorkspaceMember, Depends(require_workspace_role(*_EDIT_ROLES))]
WorkspaceViewer = Annotated[WorkspaceMember, Depends(require_workspace_role(*_VIEW_ROLES))]


def _read(media: InvitationMedia, file_url: str) -> MediaRead:
    return MediaRead(
        id=media.id,
        original_filename=media.original_filename,
        mime_type=media.mime_type,
        file_size=media.file_size,
        created_at=media.created_at,
        file_url=file_url,
    )


def _read_upload(upload: UploadFile, maximum_size: int) -> bytes:
    content = upload.file.read(maximum_size + 1)
    if len(content) > maximum_size:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Image size must not exceed {maximum_size} bytes",
        )
    return content


def _file_response(media: InvitationMedia, stream: BinaryIO) -> StreamingResponse:
    headers = {
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(media.original_filename, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        iter(lambda: stream.read(64 * 1024), b""),
        media_type=media.mime_type,
        headers=headers,
        background=BackgroundTask(stream.close),
    )


@router.post(
    "/personal/{invitation_id}/media",
    response_model=MediaRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(rate_limit("media_upload"))],
)
def upload_personal_media(
    invitation_id: UUID,
    upload: Annotated[UploadFile, File()],
    session: Database,
    storage: Storage,
    user: AuthenticatedUser,
) -> MediaRead:
    service = MediaService(session, storage)
    data = _read_upload(upload, service.max_file_size)
    media = service.upload_personal(
        user, invitation_id, upload.filename or "", upload.content_type, data
    )
    return _read(
        media,
        f"/api/v1/invitations/personal/{invitation_id}/media/{media.id}/file",
    )


@router.get(
    "/personal/{invitation_id}/media",
    response_model=list[MediaRead],
)
def list_personal_media(
    invitation_id: UUID,
    session: Database,
    storage: Storage,
    user: AuthenticatedUser,
) -> list[MediaRead]:
    media_items = MediaService(session, storage).list_personal(user, invitation_id)
    return [
        _read(
            media,
            f"/api/v1/invitations/personal/{invitation_id}/media/{media.id}/file",
        )
        for media in media_items
    ]


@router.get(
    "/personal/{invitation_id}/media/{media_id}",
    response_model=MediaRead,
)
def get_personal_media(
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    user: AuthenticatedUser,
) -> MediaRead:
    media = MediaService(session, storage).get_personal(user, invitation_id, media_id)
    return _read(
        media,
        f"/api/v1/invitations/personal/{invitation_id}/media/{media.id}/file",
    )


@router.get("/personal/{invitation_id}/media/{media_id}/file")
def get_personal_media_file(
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    user: AuthenticatedUser,
) -> StreamingResponse:
    media, stream = MediaService(session, storage).file_personal(
        user, invitation_id, media_id
    )
    return _file_response(media, stream)


@router.delete(
    "/personal/{invitation_id}/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_personal_media(
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    user: AuthenticatedUser,
) -> Response:
    MediaService(session, storage).delete_personal(user, invitation_id, media_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/{invitation_id}/media",
    response_model=MediaRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(rate_limit("media_upload"))],
)
def upload_workspace_media(
    workspace_id: UUID,
    invitation_id: UUID,
    upload: Annotated[UploadFile, File()],
    session: Database,
    storage: Storage,
    _membership: WorkspaceEditor,
) -> MediaRead:
    service = MediaService(session, storage)
    data = _read_upload(upload, service.max_file_size)
    media = service.upload_workspace(
        workspace_id,
        invitation_id,
        upload.filename or "",
        upload.content_type,
        data,
    )
    return _read(
        media,
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/media/{media.id}/file",
    )


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}/media",
    response_model=list[MediaRead],
)
def list_workspace_media(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    storage: Storage,
    _membership: WorkspaceViewer,
) -> list[MediaRead]:
    media_items = MediaService(session, storage).list_workspace(
        workspace_id, invitation_id
    )
    return [
        _read(
            media,
            f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/media/{media.id}/file",
        )
        for media in media_items
    ]


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}/media/{media_id}",
    response_model=MediaRead,
)
def get_workspace_media(
    workspace_id: UUID,
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    _membership: WorkspaceViewer,
) -> MediaRead:
    media = MediaService(session, storage).get_workspace(
        workspace_id, invitation_id, media_id
    )
    return _read(
        media,
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/media/{media.id}/file",
    )


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}/media/{media_id}/file"
)
def get_workspace_media_file(
    workspace_id: UUID,
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    _membership: WorkspaceViewer,
) -> StreamingResponse:
    media, stream = MediaService(session, storage).file_workspace(
        workspace_id, invitation_id, media_id
    )
    return _file_response(media, stream)


@router.delete(
    "/workspaces/{workspace_id}/{invitation_id}/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_workspace_media(
    workspace_id: UUID,
    invitation_id: UUID,
    media_id: UUID,
    session: Database,
    storage: Storage,
    _membership: WorkspaceEditor,
) -> Response:
    MediaService(session, storage).delete_workspace(
        workspace_id, invitation_id, media_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
