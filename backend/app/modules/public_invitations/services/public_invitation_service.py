import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation_media import InvitationMedia
from app.modules.invitation_music.repositories.invitation_music_repository import (
    InvitationMusicRepository,
)
from app.modules.invitation_music.schemas.invitation_music import PublicMusic
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.public_invitations.repositories.public_invitation_repository import (
    PublicInvitationRepository,
)
from app.modules.public_invitations.schemas.public_invitation import (
    InvitationPublishRead,
    PublicGuest,
    PublicInvitation,
    PublicInvitationResponse,
    PublicMedia,
    PublicSection,
    PublicTemplate,
)
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
logger = logging.getLogger(__name__)


class PublicInvitationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.invitations = InvitationRepository(session)
        self.repository = PublicInvitationRepository(session)
        self.music = InvitationMusicRepository(session)

    def publish_personal(
        self,
        user: User,
        invitation_id: UUID,
    ) -> InvitationPublishRead:
        invitation = self.invitations.find_personal(user.id, invitation_id, lock=True)
        return self._publish(invitation)

    def unpublish_personal(
        self,
        user: User,
        invitation_id: UUID,
    ) -> InvitationPublishRead:
        invitation = self.invitations.find_personal(user.id, invitation_id, lock=True)
        return self._unpublish(invitation)

    def publish_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> InvitationPublishRead:
        invitation = self.invitations.find_workspace(
            workspace_id, invitation_id, lock=True
        )
        return self._publish(invitation)

    def unpublish_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> InvitationPublishRead:
        invitation = self.invitations.find_workspace(
            workspace_id, invitation_id, lock=True
        )
        return self._unpublish(invitation)

    def get_public(
        self,
        slug: str,
        guest_token: str | None,
        visitor_key: str,
    ) -> PublicInvitationResponse:
        invitation = self.repository.find_published(slug)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        template = self.repository.template_for(invitation.template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        sections = self.repository.enabled_sections(invitation.id)
        media_ids: set[UUID] = set()
        section_media_ids: dict[UUID, set[UUID]] = {}
        for section in sections:
            ids = _media_ids(section.content)
            section_media_ids[section.id] = ids
            media_ids.update(ids)
        media = self.repository.media_for_ids(invitation.id, media_ids)

        public_sections = [
            PublicSection(
                section_key=section.section_key,
                section_type=section.section_type,
                title=section.title,
                content=_public_section_content(
                    section.section_type,
                    section.content,
                ),
                sort_order=section.sort_order,
                media=[
                    _public_media(item, invitation.slug)
                    for media_id in sorted(
                        section_media_ids[section.id],
                        key=str,
                    )
                    if (item := media.get(media_id)) is not None
                ],
            )
            for section in sections
        ]
        guest = self._resolve_guest(invitation.id, guest_token)
        response = PublicInvitationResponse(
            invitation=PublicInvitation(
                slug=invitation.slug,
                title=invitation.title,
                template=PublicTemplate(
                    name=template.name,
                    slug=template.slug,
                    category=template.category,
                    thumbnail_url=template.thumbnail_url,
                    preview_image_url=template.preview_image_url,
                ),
                sections=public_sections,
                music=self._public_music(invitation.id, invitation.slug),
                published_at=invitation.first_published_at or invitation.updated_at,
            ),
            guest=PublicGuest(
                is_personalized=guest is not None,
                display_name=guest.name if guest is not None else None,
            ),
        )
        self._record_view(
            invitation.id,
            hashlib.sha256(visitor_key.encode("utf-8")).hexdigest(),
            guest.id if guest is not None else None,
        )
        return response

    def public_media(
        self,
        slug: str,
        media_id: UUID,
    ) -> InvitationMedia:
        invitation = self.repository.find_published(slug)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
        sections = self.repository.enabled_sections(invitation.id)
        if not any(media_id in _media_ids(section.content) for section in sections):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
        media = self.repository.media_for_ids(invitation.id, {media_id}).get(media_id)
        if media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
        return media

    def _publish(self, invitation: Invitation | None) -> InvitationPublishRead:
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot be published",
            )
        if not _SLUG_PATTERN.fullmatch(invitation.slug):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invitation slug is invalid",
            )
        now = datetime.now(timezone.utc)
        if invitation.expires_at is not None:
            expires_at = invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Expired invitations cannot be published",
                )
        if self.session.get(Template, invitation.template_id) is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invitation template is unavailable",
            )
        self.repository.sections_for_invitation(invitation.id)
        if invitation.first_published_at is None:
            invitation.first_published_at = now
        invitation.status = "published"
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        return InvitationPublishRead.model_validate(invitation)

    def _unpublish(self, invitation: Invitation | None) -> InvitationPublishRead:
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot be unpublished",
            )
        if invitation.status != "draft":
            invitation.status = "draft"
            try:
                self.session.commit()
            except SQLAlchemyError:
                self.session.rollback()
                raise
        return InvitationPublishRead.model_validate(invitation)

    def _resolve_guest(self, invitation_id: UUID, token: str | None):
        if not token or len(token) > 160:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self.repository.find_guest_by_token(invitation_id, token_hash)

    def _record_view(
        self,
        invitation_id: UUID,
        visitor_key_hash: str,
        guest_id: UUID | None,
    ) -> None:
        try:
            self.repository.record_view(
                invitation_id,
                visitor_key_hash,
                guest_id,
            )
        except SQLAlchemyError as error:
            self.session.rollback()
            logger.error(
                "Invitation view tracking failed for invitation %s (%s)",
                invitation_id,
                type(error).__name__,
            )

    def _public_music(self, invitation_id: UUID, slug: str) -> PublicMusic | None:
        music = self.music.get_for_invitation(invitation_id)
        if music is None or not music.is_enabled:
            return None
        return PublicMusic(
            autoplay_enabled=music.autoplay_enabled,
            duration_seconds=music.duration_seconds,
            stream_url=f"/api/v1/public/invitations/{slug}/music",
        )


def _media_ids(value: Any) -> set[UUID]:
    result: set[UUID] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if "media_id" in item:
                try:
                    result.add(UUID(str(item["media_id"])))
                except (TypeError, ValueError):
                    pass
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return result


_PUBLIC_CONTENT_KEYS = {
    "heading",
    "layout",
    "images",
    "media",
    "media_id",
    "display_order",
}


def _public_section_content(section_type: str, content: Any) -> dict[str, Any]:
    if section_type in {"rsvp", "guestbook"} or not isinstance(content, dict):
        return {}
    return {
        key: _allowlisted_public_value(value, key)
        for key, value in content.items()
        if key in _PUBLIC_CONTENT_KEYS
    }


def _allowlisted_public_value(value: Any, key: str) -> Any:
    if key in {"images", "media"}:
        if not isinstance(value, list):
            return []
        return [
            {
                nested_key: nested_value
                for nested_key, nested_value in item.items()
                if nested_key in {"media_id", "display_order"}
                and isinstance(nested_value, (str, int, float, bool, type(None)))
            }
            for item in value
            if isinstance(item, dict)
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {}


def _public_media(media, slug: str) -> PublicMedia:
    return PublicMedia(
        id=media.id,
        mime_type=media.mime_type,
        file_size=media.file_size,
        file_url=f"/api/v1/public/invitations/{slug}/media/{media.id}/file",
    )


def new_guest_token() -> str:
    return secrets.token_urlsafe(32)


def guest_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
