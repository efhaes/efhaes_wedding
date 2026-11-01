from io import BytesIO
from pathlib import PurePath
from typing import BinaryIO
from uuid import UUID, uuid4
import warnings

from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation_media import InvitationMedia
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.media.repositories.media_repository import MediaRepository
from app.modules.media.storage import MediaStorage
from app.modules.users.models.user import User


_IMAGE_FORMATS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
_CANONICAL_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_IMAGE_DIMENSION = 10_000
_MAX_IMAGE_PIXELS = 40_000_000


class MediaService:
    def __init__(
        self,
        session: Session,
        storage: MediaStorage,
    ) -> None:
        self.session = session
        self.storage = storage
        self.invitations = InvitationRepository(session)
        self.media = MediaRepository(session)
        self.max_file_size = settings.MEDIA_MAX_FILE_SIZE_MB * 1024 * 1024
        self.allowed_types = {
            item.strip().lower()
            for item in settings.MEDIA_ALLOWED_IMAGE_TYPES.split(",")
            if item.strip()
        }

    def list_personal(self, user: User, invitation_id: UUID) -> list[InvitationMedia]:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self.media.list_for_invitation(invitation.id)

    def get_personal(
        self, user: User, invitation_id: UUID, media_id: UUID
    ) -> InvitationMedia:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._get(invitation.id, media_id)

    def list_workspace(self, workspace_id: UUID, invitation_id: UUID) -> list[InvitationMedia]:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self.media.list_for_invitation(invitation.id)

    def get_workspace(
        self, workspace_id: UUID, invitation_id: UUID, media_id: UUID
    ) -> InvitationMedia:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._get(invitation.id, media_id)

    def file_personal(
        self, user: User, invitation_id: UUID, media_id: UUID
    ) -> tuple[InvitationMedia, BinaryIO]:
        media = self.get_personal(user, invitation_id, media_id)
        return media, self._open_file(media)

    def file_workspace(
        self, workspace_id: UUID, invitation_id: UUID, media_id: UUID
    ) -> tuple[InvitationMedia, BinaryIO]:
        media = self.get_workspace(workspace_id, invitation_id, media_id)
        return media, self._open_file(media)

    def upload_personal(
        self,
        user: User,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> InvitationMedia:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._upload(invitation, filename, content_type, data)

    def upload_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> InvitationMedia:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._upload(invitation, filename, content_type, data)

    def delete_personal(
        self, user: User, invitation_id: UUID, media_id: UUID
    ) -> None:
        invitation = self._personal_invitation(user.id, invitation_id)
        self._delete(invitation, media_id)

    def delete_workspace(
        self, workspace_id: UUID, invitation_id: UUID, media_id: UUID
    ) -> None:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        self._delete(invitation, media_id)

    def _personal_invitation(self, owner_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_personal(owner_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def _workspace_invitation(self, workspace_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def _get(self, invitation_id: UUID, media_id: UUID) -> InvitationMedia:
        media = self.media.get_for_invitation(invitation_id, media_id)
        if media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
        return media

    def _upload(
        self,
        invitation: Invitation,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> InvitationMedia:
        self._ensure_editable(invitation)
        mime_type = self._validate_upload(filename, content_type, data)
        storage_key = f"{uuid4().hex}{_CANONICAL_EXTENSION[mime_type]}"
        self.storage.put(storage_key, data)
        original_filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:255]
        media = InvitationMedia(
            invitation_id=invitation.id,
            media_type="image",
            storage_key=storage_key,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=len(data),
        )
        self.session.add(media)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            try:
                self.storage.discard(storage_key)
            except OSError as cleanup_error:
                raise RuntimeError(
                    "Media metadata insert failed and the uploaded file could not be removed"
                ) from cleanup_error
            raise
        return media

    def _delete(self, invitation: Invitation, media_id: UUID) -> None:
        invitation = self._lock_invitation(invitation)
        self._ensure_editable(invitation)
        media = self.media.get_for_invitation(invitation.id, media_id, lock=True)
        if media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
        if self.media.has_section_reference(invitation.id, media.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Media is still referenced by an invitation section",
            )
        try:
            token = self.storage.stage_delete(media.storage_key)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Media file could not be staged for deletion",
            ) from error

        try:
            self.session.delete(media)
            self.session.commit()
        except Exception:
            self.session.rollback()
            try:
                self.storage.restore(token)
            except OSError as restore_error:
                raise RuntimeError(
                    "Media deletion failed and the quarantined file could not be restored"
                ) from restore_error
            raise
        try:
            self.storage.purge(token)
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Media was deleted but quarantined file cleanup failed",
            ) from error

    def _validate_upload(
        self,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> str:
        mime_type = (content_type or "").split(";", 1)[0].strip().lower()
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        extension = PurePath(safe_name).suffix.lower()
        if mime_type not in self.allowed_types or mime_type not in _IMAGE_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only JPEG, PNG, and WebP images are allowed",
            )
        if extension not in _IMAGE_FORMATS[mime_type]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="File extension does not match the declared image type",
            )
        if (
            not safe_name
            or len(safe_name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in safe_name)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A valid original filename is required",
            )
        if not data or len(data) > self.max_file_size:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Image size must be between 1 byte and {settings.MEDIA_MAX_FILE_SIZE_MB} MiB",
            )
        if not _matches_image_signature(mime_type, data):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Image signature does not match the declared image type",
            )
        self._decode_image(mime_type, data)
        return mime_type

    @staticmethod
    def _decode_image(mime_type: str, data: bytes) -> None:
        expected_format = {
            "image/jpeg": "JPEG",
            "image/png": "PNG",
            "image/webp": "WEBP",
        }[mime_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    width, height = image.size
                    if (
                        width > _MAX_IMAGE_DIMENSION
                        or height > _MAX_IMAGE_DIMENSION
                        or width * height > _MAX_IMAGE_PIXELS
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Image dimensions exceed the allowed limit",
                        )
                    if image.format != expected_format:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Decoded image format does not match the declared type",
                        )
                    image.verify()
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
        except HTTPException:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            UnidentifiedImageError,
            ValueError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Image file is corrupt or invalid",
            ) from error

    def _open_file(self, media: InvitationMedia) -> BinaryIO:
        if media.mime_type not in _IMAGE_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Stored media type is not allowed",
            )
        try:
            return self.storage.open(media.storage_key)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media file is unavailable",
            ) from error

    def _lock_invitation(self, invitation: Invitation) -> Invitation:
        if invitation.owner_id is not None:
            locked = self.invitations.find_personal(
                invitation.owner_id, invitation.id, lock=True
            )
        else:
            locked = self.invitations.find_workspace(
                invitation.workspace_id, invitation.id, lock=True
            )
        if locked is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return locked

    @staticmethod
    def _ensure_editable(invitation: Invitation) -> None:
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot be edited",
            )


def _matches_image_signature(mime_type: str, data: bytes) -> bool:
    if mime_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if mime_type == "image/png":
        return (
            data.startswith(b"\x89PNG\r\n\x1a\n")
            and data.endswith(b"IEND\xaeB`\x82")
        )
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False
