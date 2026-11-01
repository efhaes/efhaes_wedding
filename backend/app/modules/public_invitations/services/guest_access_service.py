from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.guest import Guest
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.public_invitations.repositories.guest_access_repository import (
    GuestAccessRepository,
)
from app.modules.public_invitations.schemas.guest_access import (
    GuestCreate,
    GuestLinkRead,
    GuestLinkRegenerated,
    GuestPage,
    GuestRead,
    GuestUpdate,
)
from app.modules.public_invitations.services.public_invitation_service import (
    guest_token_hash,
    new_guest_token,
)
from app.modules.users.models.user import User


class GuestAccessService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.invitations = InvitationRepository(session)
        self.guests = GuestAccessRepository(session)

    def create_personal(
        self,
        user: User,
        invitation_id: UUID,
        payload: GuestCreate,
    ) -> GuestLinkRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._create(invitation, payload)

    def create_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        payload: GuestCreate,
    ) -> GuestLinkRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._create(invitation, payload)

    def list_personal(
        self, user: User, invitation_id: UUID, page: int, page_size: int
    ) -> GuestPage:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._page(invitation.id, page, page_size)

    def list_workspace(
        self, workspace_id: UUID, invitation_id: UUID, page: int, page_size: int
    ) -> GuestPage:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._page(invitation.id, page, page_size)

    def get_personal(
        self, user: User, invitation_id: UUID, guest_id: UUID
    ) -> GuestRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._read(self._guest(invitation.id, guest_id))

    def get_workspace(
        self, workspace_id: UUID, invitation_id: UUID, guest_id: UUID
    ) -> GuestRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._read(self._guest(invitation.id, guest_id))

    def update_personal(
        self,
        user: User,
        invitation_id: UUID,
        guest_id: UUID,
        payload: GuestUpdate,
    ) -> GuestRead:
        invitation = self._personal_invitation(user.id, invitation_id)
        return self._update(invitation.id, guest_id, payload)

    def update_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        guest_id: UUID,
        payload: GuestUpdate,
    ) -> GuestRead:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        return self._update(invitation.id, guest_id, payload)

    def delete_personal(
        self, user: User, invitation_id: UUID, guest_id: UUID
    ) -> None:
        invitation = self._personal_invitation(user.id, invitation_id)
        self._delete(invitation.id, guest_id)

    def delete_workspace(
        self, workspace_id: UUID, invitation_id: UUID, guest_id: UUID
    ) -> None:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        self._delete(invitation.id, guest_id)

    def regenerate_personal(
        self, user: User, invitation_id: UUID, guest_id: UUID
    ) -> GuestLinkRegenerated:
        invitation = self._personal_invitation(user.id, invitation_id)
        guest, token = self._regenerate(invitation.id, guest_id)
        return GuestLinkRegenerated(invitation_url=self._link(invitation, token))

    def regenerate_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        guest_id: UUID,
    ) -> GuestLinkRegenerated:
        invitation = self._workspace_invitation(workspace_id, invitation_id)
        _, token = self._regenerate(invitation.id, guest_id)
        return GuestLinkRegenerated(invitation_url=self._link(invitation, token))

    def _personal_invitation(self, owner_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_personal(owner_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        self._ensure_manageable(invitation)
        return invitation

    def _workspace_invitation(self, workspace_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.invitations.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        self._ensure_manageable(invitation)
        return invitation

    def _create(self, invitation: Invitation, payload: GuestCreate) -> GuestLinkRead:
        token = new_guest_token()
        guest = Guest(
            invitation_id=invitation.id,
            name=payload.name,
            category=payload.category,
            invitation_token=guest_token_hash(token),
            checkin_status="pending",
        )
        self.session.add(guest)
        self._commit()
        return GuestLinkRead(
            guest=self._read(guest),
            invitation_url=self._link(invitation, token),
        )

    def _page(self, invitation_id: UUID, page: int, page_size: int) -> GuestPage:
        guests, total = self.guests.list_for_invitation(
            invitation_id,
            page=page,
            page_size=page_size,
        )
        return GuestPage(
            items=[self._read(guest) for guest in guests],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _guest(self, invitation_id: UUID, guest_id: UUID) -> Guest:
        guest = self.guests.get_for_invitation(invitation_id, guest_id)
        if guest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest not found")
        return guest

    def _update(
        self,
        invitation_id: UUID,
        guest_id: UUID,
        payload: GuestUpdate,
    ) -> GuestRead:
        guest = self.guests.get_for_invitation(invitation_id, guest_id, lock=True)
        if guest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(guest, key, value)
        self._commit()
        return self._read(guest)

    def _delete(self, invitation_id: UUID, guest_id: UUID) -> None:
        guest = self.guests.get_for_invitation(invitation_id, guest_id, lock=True)
        if guest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest not found")
        self.session.delete(guest)
        self._commit()

    def _regenerate(self, invitation_id: UUID, guest_id: UUID) -> tuple[Guest, str]:
        guest = self.guests.get_for_invitation(invitation_id, guest_id, lock=True)
        if guest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest not found")
        token = new_guest_token()
        guest.invitation_token = guest_token_hash(token)
        self._commit()
        return guest, token

    @staticmethod
    def _ensure_manageable(invitation: Invitation) -> None:
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot manage guests",
            )

    @staticmethod
    def _link(invitation: Invitation, token: str) -> str:
        frontend_url = settings.FRONTEND_URL.rstrip("/")
        return f"{frontend_url}/invitation/{invitation.slug}#to={token}"

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Guest operation conflicted with existing data",
            ) from error
        except SQLAlchemyError:
            self.session.rollback()
            raise

    @staticmethod
    def _read(guest: Guest) -> GuestRead:
        return GuestRead.model_validate(guest)
