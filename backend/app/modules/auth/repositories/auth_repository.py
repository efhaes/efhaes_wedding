from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.users.models.user import User


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_user_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def find_refresh_token(self, token_hash: str, *, for_update: bool = False) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def add_refresh_token(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        return token

    def revoke_active_tokens(
        self,
        user_id: UUID,
        revoked_at: datetime,
        reason: str,
    ) -> None:
        self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at, revoked_reason=reason)
        )
