import hmac
from collections.abc import Callable, Iterator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.database.connection import SessionLocal
from app.modules.users.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
) -> User | None:
    access_token = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if not access_token:
        return None

    try:
        payload = decode_access_token(access_token)
        user_id = UUID(str(payload["sub"]))
        session_version = int(payload["ver"])
    except (JWTError, KeyError, TypeError, ValueError):
        return None

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if user.session_version != session_version:
        return None
    return user


def require_authenticated_user(
    user: User | None = Depends(get_current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_platform_admin(
    user: User = Depends(require_authenticated_user),
) -> User:
    if user.platform_role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    return user


def require_csrf(request: Request) -> None:
    csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(settings.CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


def require_workspace_role(*allowed_roles: str) -> Callable:
    valid_roles = {"owner", "admin", "member", "editor"}
    if not allowed_roles or not set(allowed_roles).issubset(valid_roles):
        raise ValueError("At least one valid workspace role is required")

    def dependency(
        workspace_id: UUID,
        user: User = Depends(require_authenticated_user),
        session: Session = Depends(get_db),
    ) -> WorkspaceMember:
        membership = session.scalar(
            select(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.status == "active",
                WorkspaceMember.role.in_(allowed_roles),
                Workspace.status == "active",
            )
        )
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return membership

    return dependency