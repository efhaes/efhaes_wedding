from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.workspace import Workspace
from app.modules.invitations.models.invitation import Invitation
from app.modules.templates.models.template import Template


class InvitationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_published_template(self, template_id: UUID) -> Template | None:
        return self.session.scalar(
            select(Template).where(
                Template.id == template_id,
                Template.status == "published",
                Template.is_active.is_(True),
            )
        )

    def find_client(self, client_id: UUID, workspace_id: UUID) -> Client | None:
        return self.session.scalar(
            select(Client).where(
                Client.id == client_id,
                Client.workspace_id == workspace_id,
            )
        )

    def find_active_workspace(self, workspace_id: UUID) -> Workspace | None:
        return self.session.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.status == "active",
            )
        )

    def slug_exists(self, slug: str, *, exclude_id: UUID | None = None) -> bool:
        statement = select(Invitation.id).where(Invitation.slug == slug)
        if exclude_id is not None:
            statement = statement.where(Invitation.id != exclude_id)
        return self.session.scalar(statement.limit(1)) is not None

    def add(self, invitation: Invitation) -> Invitation:
        self.session.add(invitation)
        return invitation

    def find_personal(
        self,
        owner_id: UUID,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> Invitation | None:
        statement = select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.owner_id == owner_id,
                Invitation.workspace_id.is_(None),
            )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def find_workspace(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> Invitation | None:
        statement = select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.workspace_id == workspace_id,
                Invitation.owner_id.is_(None),
            )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_personal(
        self,
        owner_id: UUID,
        *,
        search: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["created_at", "updated_at"],
        sort_order: Literal["asc", "desc"],
    ) -> tuple[list[Invitation], int]:
        statement = select(Invitation).where(
            Invitation.owner_id == owner_id,
            Invitation.workspace_id.is_(None),
        )
        return self._paginate(
            statement,
            search=search,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

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
    ) -> tuple[list[Invitation], int]:
        statement = select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.owner_id.is_(None),
        )
        return self._paginate(
            statement,
            search=search,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def _paginate(
        self,
        statement,
        *,
        search: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["created_at", "updated_at"],
        sort_order: Literal["asc", "desc"],
    ) -> tuple[list[Invitation], int]:
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Invitation.title.ilike(f"%{escaped}%", escape="\\"))
        if status_filter:
            statement = statement.where(Invitation.status == status_filter)
        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        sort_column = Invitation.created_at if sort_by == "created_at" else Invitation.updated_at
        order_clause = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        statement = (
            statement.order_by(order_clause, Invitation.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement)), total
