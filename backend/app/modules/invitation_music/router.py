import re
from collections.abc import Iterator
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.core.rate_limit import rate_limit
from app.models.workspace import WorkspaceMember
from app.modules.invitation_music.schemas.invitation_music import (
    MusicRead,
    MusicSettingsUpdate,
    MusicUploadLimits,
)
from app.modules.invitation_music.services.invitation_music_service import (
    InvitationMusicService,
)
from app.modules.invitation_music.services.music_storage_service import (
    MusicStorageService,
)
from app.modules.media.storage import MediaStorage, LocalMediaStorage
from app.modules.users.models.user import User


router = APIRouter(tags=["invitation-music"])


def get_music_storage() -> MediaStorage:
    return LocalMediaStorage(settings.MUSIC_STORAGE_PATH)


Database = Annotated[Session, Depends(get_db)]
AuthenticatedUser = Annotated[User, Depends(require_authenticated_user)]
WorkspaceEditor = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor")),
]
WorkspaceViewer = Annotated[
    WorkspaceMember,
    Depends(require_workspace_role("owner", "admin", "editor", "member")),
]
MusicStorage = Annotated[MediaStorage, Depends(get_music_storage)]

_SINGLE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

def _service(session: Session, storage: MediaStorage) -> InvitationMusicService:
    return InvitationMusicService(session, MusicStorageService(storage))


def _parse_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    match = _SINGLE_RANGE.fullmatch(value.strip())
    if match is None or "," in value:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    first, last = match.groups()
    if not first and not last:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    if not first:
        suffix_length = int(last)
        if suffix_length <= 0:
            raise HTTPException(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                detail="Requested byte range is not satisfiable",
                headers={"Content-Range": f"bytes */{size}"},
            )
        start = max(size - suffix_length, 0)
        end = size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end, True


def _bounded_chunks(stream: BinaryIO, remaining: int) -> Iterator[bytes]:
    while remaining > 0:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def _stream_response(
    stream: BinaryIO,
    size: int,
    range_header: str | None,
) -> StreamingResponse:
    start, end, partial = _parse_range(range_header, size)
    stream.seek(start)
    content_length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": "inline; filename*=UTF-8''invitation-music.mp3",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    response_status = status.HTTP_206_PARTIAL_CONTENT if partial else status.HTTP_200_OK
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _bounded_chunks(stream, content_length),
        status_code=response_status,
        media_type="audio/mpeg",
        headers=headers,
        background=BackgroundTask(stream.close),
    )


@router.post(
    "/api/v1/invitations/personal/{invitation_id}/music",
    response_model=MusicRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(rate_limit("media_upload"))],
)
def upload_personal_music(
    invitation_id: UUID,
    file: Annotated[UploadFile, File()],
    session: Database,
    storage: MusicStorage,
    user: AuthenticatedUser,
) -> MusicRead:
    return _service(session, storage).upload_personal(
        user,
        invitation_id,
        file.filename or "",
        file.content_type,
        file.file,
    )


@router.get(
    "/api/v1/invitations/personal/{invitation_id}/music",
    response_model=MusicRead,
)
def get_personal_music(
    invitation_id: UUID,
    session: Database,
    storage: MusicStorage,
    user: AuthenticatedUser,
) -> MusicRead:
    return _service(session, storage).get_personal(user, invitation_id)


@router.patch(
    "/api/v1/invitations/personal/{invitation_id}/music/settings",
    response_model=MusicRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_music_settings(
    invitation_id: UUID,
    payload: MusicSettingsUpdate,
    session: Database,
    storage: MusicStorage,
    user: AuthenticatedUser,
) -> MusicRead:
    return _service(session, storage).update_personal_settings(
        user, invitation_id, payload
    )


@router.put(
    "/api/v1/invitations/personal/{invitation_id}/music",
    response_model=MusicRead,
    dependencies=[Depends(require_csrf)],
)
def replace_personal_music(
    invitation_id: UUID,
    file: Annotated[UploadFile, File()],
    session: Database,
    storage: MusicStorage,
    user: AuthenticatedUser,
) -> MusicRead:
    return _service(session, storage).replace_personal(
        user,
        invitation_id,
        file.filename or "",
        file.content_type,
        file.file,
    )


@router.delete(
    "/api/v1/invitations/personal/{invitation_id}/music",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_personal_music(
    invitation_id: UUID,
    session: Database,
    storage: MusicStorage,
    user: AuthenticatedUser,
) -> Response:
    _service(session, storage).delete_personal(user, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music",
    response_model=MusicRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def upload_workspace_music(
    workspace_id: UUID,
    invitation_id: UUID,
    file: Annotated[UploadFile, File()],
    session: Database,
    storage: MusicStorage,
    membership: WorkspaceEditor,
) -> MusicRead:
    return _service(session, storage).upload_workspace(
        workspace_id,
        invitation_id,
        file.filename or "",
        file.content_type,
        file.file,
        membership,
        replace=False,
    )


@router.get(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music",
    response_model=MusicRead,
)
def get_workspace_music(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    storage: MusicStorage,
    _membership: WorkspaceViewer,
) -> MusicRead:
    return _service(session, storage).get_workspace(workspace_id, invitation_id)


@router.patch(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music/settings",
    response_model=MusicRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_music_settings(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: MusicSettingsUpdate,
    session: Database,
    storage: MusicStorage,
    _membership: WorkspaceEditor,
) -> MusicRead:
    return _service(session, storage).update_workspace_settings(
        workspace_id, invitation_id, payload
    )


@router.put(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music",
    response_model=MusicRead,
    dependencies=[Depends(require_csrf)],
)
def replace_workspace_music(
    workspace_id: UUID,
    invitation_id: UUID,
    file: Annotated[UploadFile, File()],
    session: Database,
    storage: MusicStorage,
    _membership: WorkspaceEditor,
) -> MusicRead:
    return _service(session, storage).replace_workspace(
        workspace_id,
        invitation_id,
        file.filename or "",
        file.content_type,
        file.file,
    )


@router.delete(
    "/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_workspace_music(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    storage: MusicStorage,
    _membership: WorkspaceEditor,
) -> Response:
    _service(session, storage).delete_workspace(workspace_id, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/public/invitations/{slug}/music",
    dependencies=[Depends(rate_limit("public"))],
    summary="Stream enabled background music for a public invitation",
    description="Public endpoint; no user login or guest token is required.",
    responses={
        200: {
            "description": "Complete MPEG audio stream",
            "content": {
                "audio/mpeg": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        206: {
            "description": "Partial MPEG audio stream for a byte range request",
            "content": {
                "audio/mpeg": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        404: {"description": "Invitation or enabled music not found"},
        416: {"description": "Requested byte range is not satisfiable"},
        429: {"description": "Request rate limit exceeded"},
    },
)
def stream_public_music(
    slug: str,
    session: Database,
    storage: MusicStorage,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    service = _service(session, storage)
    music, stream = service.public_stream(slug)
    try:
        return _stream_response(stream, music.file_size, range_header)
    except HTTPException:
        stream.close()
        raise


@router.get(
    "/api/v1/invitations/music/upload-limits",
    response_model=MusicUploadLimits,
)
def music_upload_limits() -> MusicUploadLimits:
    return MusicUploadLimits(
        max_file_size_bytes=settings.MUSIC_MAX_FILE_SIZE_MB * 1024 * 1024,
        max_duration_seconds=settings.MUSIC_MAX_DURATION_SECONDS,
    )
