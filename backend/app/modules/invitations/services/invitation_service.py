import re
import unicodedata
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.models.invitation_section import InvitationSection
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.repositories.invitation_repository import InvitationRepository
from app.modules.invitations.schemas.invitation import (
    InvitationCreate,
    InvitationPage,
    InvitationRead,
    InvitationUpdate,
)
from app.modules.entitlements.services.entitlement_resolver import (
    EntitlementResolver,
)
from app.modules.users.models.user import User
from app.models.workspace import WorkspaceMember
from app.modules.templates.models.template import Template
from app.modules.templates.schemas.template import DefaultSection


class InvitationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = InvitationRepository(session)
        self.entitlements = EntitlementResolver(session)

    def create_personal(
        self,
        owner: User,
        payload: InvitationCreate,
    ) -> Invitation:
        self._validate_template(payload.template_id)
        invitation = Invitation(
            owner_id=owner.id,
            workspace_id=None,
            client_id=None,
            template_id=payload.template_id,
            title=payload.title,
            slug=self._unique_slug(payload.slug, payload.title),
            status="draft",
        )
        return self._persist_new(invitation, personal_owner_id=owner.id)

    def create_workspace(
        self,
        workspace_id: UUID,
        membership: WorkspaceMember,
        payload: InvitationCreate,
        client_id: UUID | None,
    ) -> Invitation:
        workspace = self.repository.find_active_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )
        self._validate_workspace_editor_membership(workspace_id, membership)
        template = self._validate_template(payload.template_id)
        self.entitlements.require_template_access(
            template,
            workspace_id=workspace.id,
            user_id=membership.user_id,
        )
        self._validate_client(client_id, workspace.id)
        invitation = Invitation(
            owner_id=None,
            workspace_id=workspace.id,
            client_id=client_id,
            template_id=payload.template_id,
            title=payload.title,
            slug=self._unique_slug(payload.slug, payload.title),
            status="draft",
        )
        return self._persist_new(invitation, workspace_user_id=membership.user_id)

    def list_personal(
        self,
        owner: User,
        *,
        search: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["created_at", "updated_at"],
        sort_order: Literal["asc", "desc"],
    ) -> InvitationPage:
        rows, total = self.repository.list_personal(
            owner.id,
            search=search,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return self._page(rows, total, page, page_size)

    def list_workspace(
        self,
        workspace_id: UUID,
        *,
        search: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["created_at", "updated_at"],
        sort_order: Literal["asc", "desc"],
    ) -> InvitationPage:
        if self.repository.find_active_workspace(workspace_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        rows, total = self.repository.list_workspace(
            workspace_id,
            search=search,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return self._page(rows, total, page, page_size)

    def get_personal(self, owner: User, invitation_id: UUID) -> Invitation:
        invitation = self.repository.find_personal(owner.id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def get_workspace(self, workspace_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.repository.find_workspace(workspace_id, invitation_id)
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
        return invitation

    def update_personal(
        self,
        owner: User,
        invitation_id: UUID,
        payload: InvitationUpdate,
    ) -> Invitation:
        invitation = self.get_personal(owner, invitation_id)
        return self._update(invitation, payload, workspace_id=None)

    def update_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        payload: InvitationUpdate,
        membership: WorkspaceMember,
    ) -> Invitation:
        self._validate_workspace_editor_membership(workspace_id, membership)
        invitation = self.get_workspace(workspace_id, invitation_id)
        return self._update(
            invitation,
            payload,
            workspace_id=workspace_id,
            workspace_user_id=membership.user_id,
        )

    def archive_personal(self, owner: User, invitation_id: UUID) -> Invitation:
        invitation = self.get_personal(owner, invitation_id)
        return self._archive(invitation)

    def archive_workspace(self, workspace_id: UUID, invitation_id: UUID) -> Invitation:
        invitation = self.get_workspace(workspace_id, invitation_id)
        return self._archive(invitation)

    def _update(
        self,
        invitation: Invitation,
        payload: InvitationUpdate,
        *,
        workspace_id: UUID | None,
        workspace_user_id: UUID | None = None,
    ) -> Invitation:
        if invitation.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Archived invitations cannot be updated",
            )
        changes = payload.model_dump(exclude_unset=True)
        template_id = changes.get("template_id", invitation.template_id)
        template = self._validate_template(template_id)
        if "template_id" in changes:
            self.entitlements.require_template_access(
                template,
                user_id=(
                    invitation.owner_id
                    if workspace_id is None
                    else workspace_user_id
                ),
                workspace_id=workspace_id,
                invitation_id=invitation.id if workspace_id is None else None,
            )

        if "client_id" in changes:
            client_id = changes["client_id"]
            if workspace_id is None and client_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Personal invitations cannot use workspace clients",
                )
            if workspace_id is not None:
                self._validate_client(client_id, workspace_id)
            invitation.client_id = client_id

        if "title" in changes:
            invitation.title = changes["title"]
        if "template_id" in changes:
            invitation.template_id = template_id
        if "slug" in changes:
            invitation.slug = self._unique_slug(
                changes["slug"],
                invitation.title,
                exclude_id=invitation.id,
            )
        self._commit()
        return invitation

    def _archive(self, invitation: Invitation) -> Invitation:
        if invitation.status != "archived":
            invitation.status = "archived"
            self._commit()
        return invitation

    def _validate_template(self, template_id: UUID) -> Template:
        template = self.repository.find_published_template(template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Template is missing, inactive, or not published",
            )
        return template

    def _validate_client(self, client_id: UUID | None, workspace_id: UUID) -> None:
        if client_id is not None and self.repository.find_client(client_id, workspace_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Client does not belong to this workspace",
            )

    def _persist_new(
        self,
        invitation: Invitation,
        *,
        workspace_user_id: UUID | None = None,
        personal_owner_id: UUID | None = None,
    ) -> Invitation:
        template = self.repository.find_published_template(invitation.template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Template is missing, inactive, or not published",
            )
        try:
            self.repository.add(invitation)
            self.session.flush()
            if (
                invitation.workspace_id is None
                and personal_owner_id is not None
                and template.access_level == "premium"
            ):
                self.entitlements.claim_unassigned_personal_purchase(
                    user_id=personal_owner_id,
                    invitation_id=invitation.id,
                    feature_code="premium_templates",
                )
            self._require_entitlement(
                invitation,
                template,
                workspace_user_id=workspace_user_id,
            )
            existing_section = self.session.scalar(
                select(InvitationSection.id)
                .where(InvitationSection.invitation_id == invitation.id)
                .limit(1)
            )
            if existing_section is None:
                self.session.add_all(
                    self._initial_sections(invitation.id, template)
                )
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        return invitation

    def _require_entitlement(
        self,
        invitation: Invitation,
        template: Template,
        *,
        workspace_user_id: UUID | None = None,
    ) -> None:
        if invitation.workspace_id is not None:
            self.entitlements.require_template_access(
                template,
                workspace_id=invitation.workspace_id,
                user_id=workspace_user_id,
            )
        elif invitation.owner_id is not None:
            self.entitlements.require_template_access(
                template,
                user_id=invitation.owner_id,
                invitation_id=invitation.id,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invitation entitlement scope is invalid",
            )

    @staticmethod
    def _validate_workspace_editor_membership(
        workspace_id: UUID,
        membership: WorkspaceMember,
    ) -> None:
        if (
            membership.workspace_id != workspace_id
            or membership.status != "active"
            or membership.role not in {"owner", "admin", "editor"}
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )

    @staticmethod
    def _initial_sections(
        invitation_id: UUID,
        template: Template,
    ) -> list[InvitationSection]:
        configuration = template.configuration
        defaults = configuration.get("default_sections", []) if isinstance(configuration, dict) else []
        if not isinstance(defaults, list):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template default sections are invalid",
            )
        try:
            sections = [DefaultSection.model_validate(section) for section in defaults]
        except (ValidationError, TypeError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template default sections are invalid",
            ) from error
        return [
            InvitationSection(
                invitation_id=invitation_id,
                section_key=section.key,
                section_type=section.type,
                title=section.title,
                content=section.configuration,
                is_enabled=section.is_enabled,
                sort_order=index,
            )
            for index, section in enumerate(sections)
        ]

    def _unique_slug(
        self,
        provided_slug: str | None,
        title: str,
        *,
        exclude_id: UUID | None = None,
    ) -> str:
        base = provided_slug or self._slug_base(title)
        base = base[:150].strip("-") or "invitation"
        candidate = base
        while self.repository.slug_exists(candidate, exclude_id=exclude_id):
            suffix = uuid4().hex[:8]
            candidate = f"{base[:171]}-{suffix}"
        return candidate

    @staticmethod
    def _slug_base(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "invitation"

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            message = str(error.orig).lower()
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if constraint == "invitations_slug_key" or "invitations.slug" in message:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Invitation slug already exists",
                ) from error
            raise

    @staticmethod
    def _page(
        rows: list[Invitation],
        total: int,
        page: int,
        page_size: int,
    ) -> InvitationPage:
        return InvitationPage(
            items=[InvitationRead.model_validate(row) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )
