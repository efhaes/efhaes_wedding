from sqlalchemy.orm import Session

from app.modules.auth.services.auth_service import AuthService
from app.modules.users.repositories.user_repository import UserRepository
from app.modules.users.models.user import User


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = UserRepository(session)

    def update_profile(self, user: User, name: str) -> User:
        return self.repository.update_name(user, name)

    def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        AuthService(self.session).change_password(user, current_password, new_password)
