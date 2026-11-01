from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MusicSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    autoplay_enabled: bool | None = None

    @model_validator(mode="after")
    def require_setting(self) -> "MusicSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one setting must be provided")
        if all(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Settings cannot be null")
        return self


class MusicRead(BaseModel):
    id: UUID | None
    available: bool
    is_enabled: bool
    autoplay_enabled: bool
    original_filename: str | None
    mime_type: str | None
    file_size: int | None
    duration_seconds: float | None
    created_at: datetime | None
    updated_at: datetime | None


class PublicMusic(BaseModel):
    is_enabled: bool = True
    autoplay_enabled: bool = False
    mime_type: str = "audio/mpeg"
    duration_seconds: float | None
    stream_url: str


class MusicUploadLimits(BaseModel):
    max_file_size_bytes: int = Field(gt=0)
    max_duration_seconds: int = Field(gt=0)
