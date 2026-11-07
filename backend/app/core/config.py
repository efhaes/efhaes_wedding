from typing import Literal
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Efhaess Wedding API"
    APP_ENV: str = "development"
    DEBUG: bool = False
    DATABASE_URL: str
    JWT_SECRET_KEY: SecretStr
    MIDTRANS_SERVER_KEY: SecretStr | None = None
    MIDTRANS_CLIENT_KEY: str | None = None
    MIDTRANS_IS_PRODUCTION: bool = False
    MIDTRANS_CURRENCY: str = "IDR"
    MIDTRANS_TOKEN_ENCRYPTION_KEY: SecretStr | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FRONTEND_URL: str = "http://localhost:3000"
    COOKIE_SECURE: bool | None = None
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None
    ACCESS_COOKIE_NAME: str = "access_token"
    REFRESH_COOKIE_NAME: str = "refresh_token"
    CSRF_COOKIE_NAME: str = "csrf_token"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    MEDIA_STORAGE_PATH: str = str(Path.home() / ".efhaess-wedding" / "media")
    MEDIA_MAX_FILE_SIZE_MB: int = 5
    MEDIA_ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png,image/webp"
    MUSIC_MAX_FILE_SIZE_MB: int = 10
    MUSIC_MAX_DURATION_SECONDS: int = 600
    MUSIC_STORAGE_PATH: str = str(Path.home() / ".efhaess-wedding" / "music")
    PUBLIC_REQUEST_BODY_MAX_BYTES: int = 16 * 1024
    REQUEST_BODY_MULTIPART_OVERHEAD_BYTES: int = 64 * 1024
    RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST: int = 5
    RSVP_MAX_ATTENDEES_PER_GUEST: int = 10
    RATE_LIMIT_LOGIN_REQUESTS: int = 10
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = 900
    RATE_LIMIT_REGISTER_REQUESTS: int = 5
    RATE_LIMIT_REGISTER_WINDOW_SECONDS: int = 3600
    RATE_LIMIT_REFRESH_REQUESTS: int = 30
    RATE_LIMIT_REFRESH_WINDOW_SECONDS: int = 60
    RATE_LIMIT_PUBLIC_REQUESTS: int = 60
    RATE_LIMIT_PUBLIC_WINDOW_SECONDS: int = 60
    RATE_LIMIT_SUBMISSION_REQUESTS: int = 10
    RATE_LIMIT_SUBMISSION_WINDOW_SECONDS: int = 60
    RATE_LIMIT_GUEST_ACCESS_REQUESTS: int = 30
    RATE_LIMIT_GUEST_ACCESS_WINDOW_SECONDS: int = 60
    RATE_LIMIT_MEDIA_UPLOAD_REQUESTS: int = 10
    RATE_LIMIT_MEDIA_UPLOAD_WINDOW_SECONDS: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or "change-this" in secret.lower():
            raise ValueError("JWT_SECRET_KEY must be a non-placeholder secret of at least 32 characters")
        return value

    @field_validator(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "REFRESH_TOKEN_EXPIRE_DAYS",
        "MEDIA_MAX_FILE_SIZE_MB",
        "MUSIC_MAX_FILE_SIZE_MB",
        "MUSIC_MAX_DURATION_SECONDS",
        "PUBLIC_REQUEST_BODY_MAX_BYTES",
        "REQUEST_BODY_MULTIPART_OVERHEAD_BYTES",
        "RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST",
        "RSVP_MAX_ATTENDEES_PER_GUEST",
        "RATE_LIMIT_LOGIN_REQUESTS",
        "RATE_LIMIT_LOGIN_WINDOW_SECONDS",
        "RATE_LIMIT_REGISTER_REQUESTS",
        "RATE_LIMIT_REGISTER_WINDOW_SECONDS",
        "RATE_LIMIT_REFRESH_REQUESTS",
        "RATE_LIMIT_REFRESH_WINDOW_SECONDS",
        "RATE_LIMIT_PUBLIC_REQUESTS",
        "RATE_LIMIT_PUBLIC_WINDOW_SECONDS",
        "RATE_LIMIT_SUBMISSION_REQUESTS",
        "RATE_LIMIT_SUBMISSION_WINDOW_SECONDS",
        "RATE_LIMIT_GUEST_ACCESS_REQUESTS",
        "RATE_LIMIT_GUEST_ACCESS_WINDOW_SECONDS",
        "RATE_LIMIT_MEDIA_UPLOAD_REQUESTS",
        "RATE_LIMIT_MEDIA_UPLOAD_WINDOW_SECONDS",
    )
    @classmethod
    def validate_positive_token_lifetime(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Configured lifetimes and file-size limits must be positive")
        return value

    @model_validator(mode="after")
    def validate_cookie_settings(self) -> "Settings":
        if self.COOKIE_SAMESITE == "none" and self.COOKIE_SECURE is False:
            raise ValueError("SameSite=None cookies require COOKIE_SECURE=true")
        if self.RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST > self.RSVP_MAX_ATTENDEES_PER_GUEST:
            raise ValueError(
                "RSVP_DEFAULT_MAX_ATTENDEES_PER_GUEST cannot exceed RSVP_MAX_ATTENDEES_PER_GUEST"
            )
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.APP_ENV.lower() not in {"development", "dev", "test"}

    @property
    def midtrans_api_base_url(self) -> str:
        if self.MIDTRANS_IS_PRODUCTION:
            return "https://api.midtrans.com"
        return "https://api.sandbox.midtrans.com"

    @property
    def midtrans_snap_base_url(self) -> str:
        if self.MIDTRANS_IS_PRODUCTION:
            return "https://app.midtrans.com"
        return "https://app.sandbox.midtrans.com"

    def require_midtrans_server_key(self) -> SecretStr:
        if (
            self.MIDTRANS_SERVER_KEY is None
            or not self.MIDTRANS_SERVER_KEY.get_secret_value().strip()
        ):
            raise RuntimeError("MIDTRANS_SERVER_KEY must be configured to use Midtrans")
        return self.MIDTRANS_SERVER_KEY

    def require_midtrans_token_encryption_key(self) -> SecretStr:
        key = self.MIDTRANS_TOKEN_ENCRYPTION_KEY
        if key is None or len(key.get_secret_value()) < 32:
            raise RuntimeError(
                "MIDTRANS_TOKEN_ENCRYPTION_KEY must be configured for checkout"
            )
        return key

    def require_midtrans_currency(self) -> str:
        currency = self.MIDTRANS_CURRENCY.strip().upper()
        if (
            len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
        ):
            raise RuntimeError(
                "MIDTRANS_CURRENCY must be a three-letter currency code"
            )
        return currency

    def require_midtrans_client_key(self) -> str:
        if self.MIDTRANS_CLIENT_KEY is None or not self.MIDTRANS_CLIENT_KEY.strip():
            raise RuntimeError("MIDTRANS_CLIENT_KEY must be configured for frontend use")
        return self.MIDTRANS_CLIENT_KEY


settings = Settings()