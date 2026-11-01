import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import settings


_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _password_hasher.hash(
    "constant-time-login-check-not-a-real-password"
)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def dummy_password_hash() -> str:
    return _DUMMY_PASSWORD_HASH


def create_access_token(user_id: UUID, session_version: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "type": "access",
            "ver": session_version,
            "iat": int(now.timestamp()),
            "exp": expires_at,
        },
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> dict[str, object]:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithms=[settings.JWT_ALGORITHM],
    )
    if payload.get("type") != "access":
        raise JWTError("Invalid token type")
    return payload


def create_refresh_token() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return token, expires_at


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip() or None


def sign_visitor_identifier(identifier: str) -> str:
    signature = hmac.new(
        settings.JWT_SECRET_KEY.get_secret_value().encode("utf-8"),
        f"public-viewer:v1:{identifier}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{identifier}.{signature}"


def verify_visitor_identifier(value: str | None) -> str | None:
    if not value:
        return None
    identifier, separator, signature = value.partition(".")
    if (
        not separator
        or not signature
        or not 32 <= len(identifier) <= 100
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in identifier)
    ):
        return None
    expected = sign_visitor_identifier(identifier).partition(".")[2]
    return identifier if hmac.compare_digest(signature, expected) else None