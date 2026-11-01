from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


WorkspaceRole = Literal["owner", "admin", "editor", "member"]
AssignableWorkspaceRole = Literal["admin", "editor", "member"]


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: AssignableWorkspaceRole


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AssignableWorkspaceRole


class MemberUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: EmailStr


class MemberRead(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    status: str
    joined_at: datetime | None
    created_at: datetime
    updated_at: datetime
    user: MemberUserRead

    model_config = ConfigDict(from_attributes=True)
