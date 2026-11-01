from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceMember
from app.modules.users.models.user import User
from app.modules.workspaces.repositories.member_repository import MemberRepository
from app.modules.workspaces.schemas.member import AssignableWorkspaceRole, MemberCreate, MemberRoleUpdate


class MemberService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = MemberRepository(session)

    def list_members(self, workspace_id: UUID) -> list[WorkspaceMember]:
        return self.repository.list_for_workspace(workspace_id)

    def get_member(self, workspace_id: UUID, member_id: UUID) -> WorkspaceMember:
        member = self.repository.find_by_id(workspace_id, member_id)
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
        return member

    def add_member(
        self,
        workspace: Workspace,
        actor_membership: WorkspaceMember,
        payload: MemberCreate,
    ) -> WorkspaceMember:
        role = self._normalize_role(payload.role)
        if actor_membership.role == "admin" and role == "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot add another admin",
            )

        user = self.repository.find_user_by_email(str(payload.email).strip().lower())
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.id == workspace.owner_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace owner is already a member",
            )

        member = self.repository.find_for_user(workspace.id, user.id)
        if member is not None and member.status == "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already an active member",
            )

        if member is None:
            member = WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
                status="active",
                joined_at=datetime.now(timezone.utc),
            )
            self.session.add(member)
        else:
            member.role = role
            member.status = "active"
            member.joined_at = datetime.now(timezone.utc)

        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a workspace member",
            ) from error
        return self.repository.find_by_id(workspace.id, member.id) or member

    def update_member_role(
        self,
        workspace: Workspace,
        actor_membership: WorkspaceMember,
        member_id: UUID,
        payload: MemberRoleUpdate,
    ) -> WorkspaceMember:
        target = self.get_member(workspace.id, member_id)
        self._ensure_target_is_not_owner(workspace, target)
        new_role = self._normalize_role(payload.role)
        if actor_membership.role == "admin" and (
            target.role == "admin" or new_role == "admin"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot manage admin memberships",
            )
        target.role = new_role
        self.session.commit()
        return target

    def remove_member(
        self,
        workspace: Workspace,
        actor_membership: WorkspaceMember,
        member_id: UUID,
    ) -> None:
        target = self.get_member(workspace.id, member_id)
        self._ensure_target_is_not_owner(workspace, target)
        if actor_membership.role == "admin" and target.role == "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot remove another admin",
            )
        target.status = "suspended"
        self.session.commit()

    @staticmethod
    def _ensure_target_is_not_owner(
        workspace: Workspace,
        target: WorkspaceMember,
    ) -> None:
        if target.user_id == workspace.owner_id or target.role == "owner":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspace owner cannot be changed or removed here",
            )

    @staticmethod
    def _normalize_role(role: AssignableWorkspaceRole) -> str:
        return str(role).lower()
