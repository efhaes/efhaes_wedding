from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import UUID

from app.modules.users.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == user_id))

    def add(self, user: User) -> User:
        self.session.add(user)
        return user

    def update_name(self, user: User, name: str) -> User:
        user.name = name
        self.session.commit()
        return user
