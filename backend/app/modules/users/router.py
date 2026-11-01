from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_authenticated_user, require_csrf
from app.modules.auth.schemas.auth import PasswordChangeRequest
from app.modules.users.models.user import User
from app.modules.users.schemas.user import UserProfileUpdate, UserRead
from app.modules.users.services.user_service import UserService


router = APIRouter(prefix="/users", tags=["users"])


@router.patch(
    "/me",
    response_model=UserRead,
    dependencies=[Depends(require_csrf)],
)
def update_profile(
    payload: UserProfileUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> User:
    return UserService(session).update_profile(user, payload.name)


@router.patch(
    "/me/password",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def change_password(
    payload: PasswordChangeRequest,
    session: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> None:
    UserService(session).change_password(
        user,
        payload.current_password,
        payload.new_password,
    )
