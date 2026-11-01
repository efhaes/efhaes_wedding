import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.models.rate_limit_bucket import RateLimitBucket


class RateLimitStorage(Protocol):
    def consume(
        self,
        session: Session,
        bucket_key: str,
        *,
        now: datetime,
        limit: int,
        window_seconds: int,
    ) -> bool: ...


class DatabaseRateLimitStorage:
    def consume(
        self,
        session: Session,
        bucket_key: str,
        *,
        now: datetime,
        limit: int,
        window_seconds: int,
    ) -> bool:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            insert = postgresql_insert
        elif dialect == "sqlite":
            insert = sqlite_insert
        else:
            raise RuntimeError(f"Rate limiting is unsupported for {dialect}")

        reset_at = now + timedelta(seconds=window_seconds)
        expired = RateLimitBucket.reset_at <= now
        statement = insert(RateLimitBucket).values(
            bucket_key=bucket_key,
            window_started_at=now,
            reset_at=reset_at,
            request_count=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[RateLimitBucket.bucket_key],
            set_={
                "window_started_at": case(
                    (expired, now),
                    else_=RateLimitBucket.window_started_at,
                ),
                "reset_at": case((expired, reset_at), else_=RateLimitBucket.reset_at),
                "request_count": case(
                    (expired, 1),
                    (
                        RateLimitBucket.request_count < limit + 1,
                        RateLimitBucket.request_count + 1,
                    ),
                    else_=limit + 1,
                ),
            },
        ).returning(RateLimitBucket.request_count)

        request_count = session.scalar(statement)
        if request_count is None:
            raise RuntimeError("Rate limit storage did not return a request count")
        if secrets.randbelow(100) == 0:
            session.execute(
                delete(RateLimitBucket).where(RateLimitBucket.reset_at < now)
            )
        session.commit()
        return request_count <= limit


_POLICIES: dict[str, tuple[str, str]] = {
    "login": ("RATE_LIMIT_LOGIN_REQUESTS", "RATE_LIMIT_LOGIN_WINDOW_SECONDS"),
    "register": ("RATE_LIMIT_REGISTER_REQUESTS", "RATE_LIMIT_REGISTER_WINDOW_SECONDS"),
    "refresh": ("RATE_LIMIT_REFRESH_REQUESTS", "RATE_LIMIT_REFRESH_WINDOW_SECONDS"),
    "public": ("RATE_LIMIT_PUBLIC_REQUESTS", "RATE_LIMIT_PUBLIC_WINDOW_SECONDS"),
    "submission": (
        "RATE_LIMIT_SUBMISSION_REQUESTS",
        "RATE_LIMIT_SUBMISSION_WINDOW_SECONDS",
    ),
    "guest_access": (
        "RATE_LIMIT_GUEST_ACCESS_REQUESTS",
        "RATE_LIMIT_GUEST_ACCESS_WINDOW_SECONDS",
    ),
    "media_upload": (
        "RATE_LIMIT_MEDIA_UPLOAD_REQUESTS",
        "RATE_LIMIT_MEDIA_UPLOAD_WINDOW_SECONDS",
    ),
}

_storage: RateLimitStorage = DatabaseRateLimitStorage()


def set_rate_limit_storage(storage: RateLimitStorage) -> None:
    global _storage
    _storage = storage


def get_rate_limit_storage() -> RateLimitStorage:
    return _storage


def rate_limit(policy_name: str) -> Callable[..., None]:
    if policy_name not in _POLICIES:
        raise ValueError(f"Unknown rate limit policy: {policy_name}")

    def enforce(request: Request, session: Session = Depends(get_db)) -> None:
        max_setting, window_setting = _POLICIES[policy_name]
        limit = int(getattr(settings, max_setting))
        window_seconds = int(getattr(settings, window_setting))
        remote_host = request.client.host if request.client is not None else "unknown"
        material = f"rate-limit:{policy_name}:{remote_host}".encode("utf-8")
        bucket_key = hmac.new(
            settings.JWT_SECRET_KEY.get_secret_value().encode("utf-8"),
            material,
            hashlib.sha256,
        ).hexdigest()
        allowed = get_rate_limit_storage().consume(
            session,
            bucket_key,
            now=datetime.now(timezone.utc),
            limit=limit,
            window_seconds=window_seconds,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )

    return enforce
