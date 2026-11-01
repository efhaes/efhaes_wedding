from pathlib import PurePath
from typing import BinaryIO
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import WorkspaceMember
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.invitation_music.models.invitation_music import InvitationMusic
from app.modules.invitation_music.repositories.invitation_music_repository import (
    InvitationMusicRepository,
)
from app.modules.invitation_music.schemas.invitation_music import (
    MusicRead,
    MusicSettingsUpdate,
    PublicMusic,
)
from app.modules.invitation_music.services.music_storage_service import (
    MusicStorageService,
)
from app.modules.public_invitations.repositories.public_invitation_repository import (
    PublicInvitationRepository,
)
from app.modules.users.models.user import User


class InvitationMusicService:
    def __init__(
        self,
        session: Session,
        storage: MusicStorageService,
    ) -> None:
        self.session = session
        self.storage = storage
        self.invitations = InvitationRepository(session)
        self.music = InvitationMusicRepository(session)
        self.public_invitations = PublicInvitationRepository(session)

    def get_personal(self, user: User, invitation_id: UUID) -> MusicRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._read(self.music.get_for_invitation(invitation.id))

    def get_workspace(
        self, workspace_id: UUID, invitation_id: UUID
    ) -> MusicRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._read(self.music.get_for_invitation(invitation.id))

    def upload_personal(
        self,
        user: User,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> MusicRead:
        invitation = self._personal_invitation(user.id, invitation_id, lock=True)
        return self._upload(invitation, filename, content_type, source, replace=False)

    def upload_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
        _membership: WorkspaceMember,
        *,
        replace: bool,
    ) -> MusicRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id, lock=True)
        return self._upload(invitation, filename, content_type, source, replace=replace)

    def replace_personal(
        self,
        user: User,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> MusicRead:
        invitation = self._personal_invitation(user.id, invitation_id, lock=True)
        return self._upload(invitation, filename, content_type, source, replace=True)

    def update_personal_settings(
        self,
        user: User,
        invitation_id: UUID,
        payload: MusicSettingsUpdate,
    ) -> MusicRead:
        invitation = self._personal_invitation(user.id, invitation_id, lock=True)
        return self._update_settings(invitation, payload)

    def update_workspace_settings(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        payload: MusicSettingsUpdate,
    ) -> MusicRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id, lock=True)
        return self._update_settings(invitation, payload)

    def replace_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> MusicRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id, lock=True)
        return self._upload(invitation, filename, content_type, source, replace=True)

    def delete_personal(self, user: User, invitation_id: UUID) -> None:
        invitation = self._personal_invitation(user.id, invitation_id, lock=True)
        self._delete(invitation)

    def delete_workspace(self, workspace_id: UUID, invitation_id: UUID) -> None:
        invitation = self._workspace_invitation(workspace_id, invitation_id, lock=True)
        self._delete(invitation)

    def public_configuration(self, invitation: Invitation) -> PublicMusic | None:
        music = self.music.get_for_invitation(invitation.id)
        if music is None or not music.is_enabled:
            return None
        return PublicMusic(
            autoplay_enabled=music.autoplay_enabled,
            duration_seconds=music.duration_seconds,
            stream_url=f"/api/v1/public/invitations/{invitation.slug}/music",
        )

    def public_stream(
        self,
        slug: str,
    ) -> tuple[InvitationMusic, BinaryIO]:
        invitation = self._published_invitation(slug)
        music = self.music.get_for_invitation(invitation.id)
        if music is None or not music.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Music not found",
            )
        try:
            return music, self.storage.open(music.storage_key)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Music not found",
            ) from error

    def _upload(
        self,
        invitation: Invitation,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
        *,
        replace: bool,
    ) -> MusicRead:
        self._ensure_editable(invitation)
        existing = self.music.get_for_invitation(invitation.id, lock=True)
        if existing is not None and not replace:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invitation already has background music; use replace",
            )
        if existing is None and replace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation music not found",
            )

        storage_key, safe_filename, file_size, duration = self.storage.store_upload(
            source, filename, content_type
        )
        staged_old: str | None = None
        if existing is not None:
            try:
                staged_old = self.storage.stage_delete(existing.storage_key)
            except (OSError, ValueError):
                self._discard_new(storage_key)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Existing music could not be safely replaced",
                )
            existing.storage_key = storage_key
            existing.original_filename = safe_filename
            existing.mime_type = "audio/mpeg"
            existing.file_size = file_size
            existing.duration_seconds = duration
            existing.updated_at = _now_after(existing.updated_at)
            music = existing
        else:
            music = InvitationMusic(
                invitation_id=invitation.id,
                storage_key=storage_key,
                original_filename=safe_filename,
                mime_type="audio/mpeg",
                file_size=file_size,
                duration_seconds=duration,
                is_enabled=False,
                autoplay_enabled=False,
            )
            self.session.add(music)

        try:
            self.session.commit()
        except SQLAlchemyError as error:
            self.session.rollback()
            if staged_old is not None:
                self._restore(staged_old)
            self._discard_new(storage_key)
            if isinstance(error, IntegrityError):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Invitation already has background music",
                ) from error
            raise

        if staged_old is not None:
            try:
                self.storage.purge(staged_old)
            except (OSError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Music was replaced; old-file cleanup is pending",
                ) from error
        return self._read(music)

    def _update_settings(
        self,
        invitation: Invitation,
        payload: MusicSettingsUpdate,
    ) -> MusicRead:
        self._ensure_editable(invitation)
        music = self.music.get_for_invitation(invitation.id, lock=True)
        if music is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation music not found",
            )
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(music, field, value)
        music.updated_at = _now_after(music.updated_at)
        self._commit()
        return self._read(music)

    def _delete(self, invitation: Invitation) -> None:
        self._ensure_editable(invitation)
        music = self.music.get_for_invitation(invitation.id, lock=True)
        if music is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation music not found",
            )
        try:
            staged = self.storage.stage_delete(music.storage_key)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Music could not be safely deleted",
            ) from error
        self.session.delete(music)
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            self._restore(staged)
            raise
        try:
            self.storage.purge(staged)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Music metadata was deleted; file cleanup is pending",
            ) from error

    def _personal_invitation(
        self,
        owner_id: UUID,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> Invitation:
        invitation = self.invitations.find_personal(
            owner_id, invitation_id, lock=lock
        )
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return invitation

    def _workspace_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> Invitation:
        invitation = self.invitations.find_workspace(
            workspace_id, invitation_id, lock=lock
        )
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        return invitation

    def _published_invitation(self, slug: str) -> Invitation:
        invitation = self.public_invitations.find_published(slug)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Music not found",
            )
        return invitation

    @staticmethod
    def _ensure_editable(invitation: Invitation) -> None:
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot manage music",
            )

    def _commit(self) -> None:
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise

    def _restore(self, token: str) -> None:
        try:
            self.storage.restore(token)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                "Database operation failed and previous music could not be restored"
            ) from error

    def _discard_new(self, storage_key: str) -> None:
        try:
            self.storage.discard(storage_key)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                "Database operation failed and uploaded music could not be removed"
            ) from error

    @staticmethod
    def _read(music: InvitationMusic | None) -> MusicRead:
        if music is None:
            return MusicRead(
                id=None,
                available=False,
                is_enabled=False,
                autoplay_enabled=False,
                original_filename=None,
                mime_type=None,
                file_size=None,
                duration_seconds=None,
                created_at=None,
                updated_at=None,
            )
        return MusicRead(
            id=music.id,
            available=True,
            is_enabled=music.is_enabled,
            autoplay_enabled=music.autoplay_enabled,
            original_filename=music.original_filename,
            mime_type=music.mime_type,
            file_size=music.file_size,
            duration_seconds=music.duration_seconds,
            created_at=music.created_at,
            updated_at=music.updated_at,
        )


def _now_after(value):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return max(now, value + timedelta(microseconds=1))
