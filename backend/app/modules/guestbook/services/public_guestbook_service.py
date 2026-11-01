from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.guestbook import GuestbookEntry
from app.modules.guestbook.repositories.guestbook_repository import (
    GuestbookRepository,
)
from app.modules.guestbook.schemas.guestbook import (
    PublicGuestbookEntryRead,
    PublicGuestbookPage,
    PublicGuestbookSubmission,
)
from app.modules.guestbook.services.guestbook_configuration import (
    commit_guestbook,
    ensure_section_enabled,
    section_configuration,
)
from app.modules.public_invitations.services.public_invitation_service import (
    guest_token_hash,
)


class PublicGuestbookService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = GuestbookRepository(session)

    def submit(
        self,
        slug: str,
        payload: PublicGuestbookSubmission,
    ) -> PublicGuestbookEntryRead:
        invitation = self.repository.find_public_invitation(slug, lock=True)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        section = self.repository.get_section(invitation.id, lock=True)
        ensure_section_enabled(section)
        configuration = section_configuration(section)
        if not configuration.allow_guest_message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Guestbook messages are not allowed",
            )
        guest_id: UUID | None = None
        guest_name = payload.guest_name
        if payload.guest_token is not None:
            guest = self.repository.find_guest_by_token(
                invitation.id,
                guest_token_hash(payload.guest_token),
                lock=True,
            )
            if guest is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Invitation or guest token not found",
                )
            guest_id = guest.id
            guest_name = guest.name
        elif not configuration.allow_anonymous:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Personalized guest access is required",
            )
        if guest_name is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Guest name is required",
            )

        entry_status = (
            "pending" if configuration.moderation_required else "approved"
        )
        entry = GuestbookEntry(
            invitation_id=invitation.id,
            guest_id=guest_id,
            guest_name=guest_name,
            message=payload.message,
            status=entry_status,
            is_approved=entry_status == "approved",
        )
        self.repository.add(entry)
        commit_guestbook(self.session)
        return PublicGuestbookEntryRead(
            guest_name=entry.guest_name,
            message=entry.message,
            created_at=entry.created_at,
        )

    def public_page(
        self,
        slug: str,
        *,
        page: int,
        page_size: int,
    ) -> PublicGuestbookPage:
        invitation = self.repository.find_public_invitation(slug)
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found",
            )
        section = self.repository.get_section(invitation.id)
        ensure_section_enabled(section)
        entries, total = self.repository.public_page(
            invitation.id,
            page=page,
            page_size=page_size,
        )
        return PublicGuestbookPage(
            items=[
                PublicGuestbookEntryRead(
                    guest_name=entry.guest_name,
                    message=entry.message,
                    created_at=entry.created_at,
                )
                for entry in entries
            ],
            page=page,
            page_size=page_size,
            total=total,
            has_next=page * page_size < total,
        )
