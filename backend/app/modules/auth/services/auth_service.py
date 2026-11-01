from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    dummy_password_hash,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.modules.auth.repositories.auth_repository import AuthRepository
from app.modules.auth.schemas.auth import LoginRequest, RegisterRequest
from app.modules.users.models.user import User


class AuthService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AuthRepository(session)

    def register(self, request: RegisterRequest) -> None:
        password_hash = hash_password(request.password)
        if self.repository.find_user_by_email(request.email) is not None:
            return
        user = User(
            name=request.name,
            email=request.email,
            password_hash=password_hash,
            platform_role="user",
        )
        self.session.add(user)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            message = str(error.orig).lower()
            if constraint == "ix_users_email" or "users.email" in message:
                return
            raise

    def login(self, request: LoginRequest) -> tuple[User, str, str]:
        user = self.repository.find_user_by_email(request.email)
        stored_hash = user.password_hash if user is not None else dummy_password_hash()
        password_matches = verify_password(request.password, stored_hash)
        if user is None or not password_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is incorrect",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is incorrect",
            )

        access_token, _ = create_access_token(user.id, user.session_version)
        refresh_token, refresh_expires_at = create_refresh_token()
        self.repository.add_refresh_token(
            user.id, hash_refresh_token(refresh_token), refresh_expires_at
        )
        self.session.commit()
        return user, access_token, refresh_token

    def refresh(self, raw_token: str) -> tuple[User, str, str]:
        token_hash = hash_refresh_token(raw_token)
        now = datetime.now(timezone.utc)
        invalid = False
        access_token: str | None = None
        rotated_token: str | None = None
        refreshed_user: User | None = None

        with self.session.begin():
            old_token = self.repository.find_refresh_token(token_hash, for_update=True)
            if old_token is None:
                invalid = True
            elif old_token.revoked_at is not None:
                if old_token.revoked_reason == "rotated":
                    self.repository.revoke_active_tokens(
                        old_token.user_id, now, "reuse_detected"
                    )
                invalid = True
            elif _as_utc(old_token.expires_at) <= now:
                old_token.revoked_at = now
                old_token.revoked_reason = "expired"
                invalid = True
            else:
                user = self.session.get(User, old_token.user_id)
                if user is None or not user.is_active:
                    old_token.revoked_at = now
                    old_token.revoked_reason = "inactive_account"
                    invalid = True
                else:
                    old_token.revoked_at = now
                    old_token.revoked_reason = "rotated"
                    rotated_token, refresh_expires_at = create_refresh_token()
                    self.repository.add_refresh_token(
                        user.id,
                        hash_refresh_token(rotated_token),
                        refresh_expires_at,
                    )
                    access_token, _ = create_access_token(user.id, user.session_version)
                    refreshed_user = user

        if invalid or access_token is None or rotated_token is None or refreshed_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token is invalid or expired",
            )
        return refreshed_user, access_token, rotated_token

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        token_hash = hash_refresh_token(raw_token)
        now = datetime.now(timezone.utc)
        with self.session.begin():
            token = self.repository.find_refresh_token(token_hash, for_update=True)
            if token is not None and token.revoked_at is None:
                token.revoked_at = now
                token.revoked_reason = "logout"

    def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

        now = datetime.now(timezone.utc)
        user.password_hash = hash_password(new_password)
        user.session_version += 1
        self.repository.revoke_active_tokens(user.id, now, "password_changed")
        self.session.commit()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
