from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db,
    require_authenticated_user,
    require_csrf,
    require_workspace_role,
)
from app.models.invitation_section import InvitationSection
from app.models.workspace import WorkspaceMember
from app.modules.invitation_editor.schemas.section import (
    SectionEnabledUpdate,
    SectionRead,
    SectionReorder,
    SectionUpdate,
)
from app.modules.invitation_editor.services.section_service import SectionService
from app.modules.users.models.user import User


router = APIRouter(prefix="/invitations", tags=["invitation-editor"])
_VIEW_ROLES = ("owner", "admin", "editor", "member")
_EDIT_ROLES = ("owner", "admin", "editor")

Database = Annotated[Session, Depends(get_db)]
AuthenticatedUser = Annotated[User, Depends(require_authenticated_user)]
WorkspaceEditor = Annotated[WorkspaceMember, Depends(require_workspace_role(*_EDIT_ROLES))]
WorkspaceViewer = Annotated[WorkspaceMember, Depends(require_workspace_role(*_VIEW_ROLES))]


def _read(section: InvitationSection) -> SectionRead:
    return SectionRead.model_validate(section)


@router.get(
    "/personal/{invitation_id}/sections",
    response_model=list[SectionRead],
)
def list_personal_sections(
    invitation_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> list[SectionRead]:
    return [
        _read(section)
        for section in SectionService(session).list_personal(user, invitation_id)
    ]


@router.put(
    "/personal/{invitation_id}/sections/reorder",
    response_model=list[SectionRead],
    dependencies=[Depends(require_csrf)],
)
def reorder_personal_sections(
    invitation_id: UUID,
    payload: SectionReorder,
    session: Database,
    user: AuthenticatedUser,
) -> list[SectionRead]:
    return [
        _read(section)
        for section in SectionService(session).reorder_personal(
            user, invitation_id, payload
        )
    ]


@router.get(
    "/personal/{invitation_id}/sections/{section_id}",
    response_model=SectionRead,
)
def get_personal_section(
    invitation_id: UUID,
    section_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> SectionRead:
    return _read(
        SectionService(session).get_personal(user, invitation_id, section_id)
    )


@router.patch(
    "/personal/{invitation_id}/sections/{section_id}",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def update_personal_section(
    invitation_id: UUID,
    section_id: UUID,
    payload: SectionUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> SectionRead:
    return _read(
        SectionService(session).update_personal(
            user, invitation_id, section_id, payload
        )
    )


@router.patch(
    "/personal/{invitation_id}/sections/{section_id}/enabled",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def set_personal_section_enabled(
    invitation_id: UUID,
    section_id: UUID,
    payload: SectionEnabledUpdate,
    session: Database,
    user: AuthenticatedUser,
) -> SectionRead:
    return _read(
        SectionService(session).set_enabled_personal(
            user, invitation_id, section_id, payload
        )
    )


@router.post(
    "/personal/{invitation_id}/sections/{section_id}/reset",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def reset_personal_section(
    invitation_id: UUID,
    section_id: UUID,
    session: Database,
    user: AuthenticatedUser,
) -> SectionRead:
    return _read(
        SectionService(session).reset_personal(user, invitation_id, section_id)
    )


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}/sections",
    response_model=list[SectionRead],
)
def list_workspace_sections(
    workspace_id: UUID,
    invitation_id: UUID,
    session: Database,
    _membership: WorkspaceViewer,
) -> list[SectionRead]:
    return [
        _read(section)
        for section in SectionService(session).list_workspace(
            workspace_id, invitation_id
        )
    ]


@router.put(
    "/workspaces/{workspace_id}/{invitation_id}/sections/reorder",
    response_model=list[SectionRead],
    dependencies=[Depends(require_csrf)],
)
def reorder_workspace_sections(
    workspace_id: UUID,
    invitation_id: UUID,
    payload: SectionReorder,
    session: Database,
    _membership: WorkspaceEditor,
) -> list[SectionRead]:
    return [
        _read(section)
        for section in SectionService(session).reorder_workspace(
            workspace_id, invitation_id, payload
        )
    ]


@router.get(
    "/workspaces/{workspace_id}/{invitation_id}/sections/{section_id}",
    response_model=SectionRead,
)
def get_workspace_section(
    workspace_id: UUID,
    invitation_id: UUID,
    section_id: UUID,
    session: Database,
    _membership: WorkspaceViewer,
) -> SectionRead:
    return _read(
        SectionService(session).get_workspace(
            workspace_id, invitation_id, section_id
        )
    )


@router.patch(
    "/workspaces/{workspace_id}/{invitation_id}/sections/{section_id}",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_section(
    workspace_id: UUID,
    invitation_id: UUID,
    section_id: UUID,
    payload: SectionUpdate,
    session: Database,
    _membership: WorkspaceEditor,
) -> SectionRead:
    return _read(
        SectionService(session).update_workspace(
            workspace_id, invitation_id, section_id, payload
        )
    )


@router.patch(
    "/workspaces/{workspace_id}/{invitation_id}/sections/{section_id}/enabled",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def set_workspace_section_enabled(
    workspace_id: UUID,
    invitation_id: UUID,
    section_id: UUID,
    payload: SectionEnabledUpdate,
    session: Database,
    _membership: WorkspaceEditor,
) -> SectionRead:
    return _read(
        SectionService(session).set_enabled_workspace(
            workspace_id, invitation_id, section_id, payload
        )
    )


@router.post(
    "/workspaces/{workspace_id}/{invitation_id}/sections/{section_id}/reset",
    response_model=SectionRead,
    dependencies=[Depends(require_csrf)],
)
def reset_workspace_section(
    workspace_id: UUID,
    invitation_id: UUID,
    section_id: UUID,
    session: Database,
    _membership: WorkspaceEditor,
) -> SectionRead:
    return _read(
        SectionService(session).reset_workspace(
            workspace_id, invitation_id, section_id
        )
    )
