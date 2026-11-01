from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.invitation_music.schemas.invitation_music import PublicMusic


class PublicTemplate(BaseModel):
    name: str
    slug: str
    category: str
    thumbnail_url: str | None
    preview_image_url: str | None


class PublicMedia(BaseModel):
    """Public file metadata without internal storage paths or keys."""

    id: UUID
    mime_type: str
    file_size: int
    file_url: str


class PublicSection(BaseModel):
    section_key: str
    section_type: str
    title: str
    content: dict[str, Any] = Field(
        description=(
            "Allow-listed public content. Supported top-level keys are heading, "
            "layout, images, media, media_id, and display_order. RSVP and "
            "guestbook configuration is intentionally omitted."
        )
    )
    sort_order: int
    media: list[PublicMedia] = Field(
        description="Only media referenced by this enabled section and invitation."
    )


class PublicInvitation(BaseModel):
    slug: str
    title: str
    template: PublicTemplate
    sections: list[PublicSection]
    music: PublicMusic | None = None
    published_at: datetime


class PublicGuest(BaseModel):
    is_personalized: bool
    display_name: str | None


class PublicInvitationResponse(BaseModel):
    invitation: PublicInvitation
    guest: PublicGuest


class InvitationViewCount(BaseModel):
    invitation_id: UUID
    view_count: int


class InvitationPublishRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    status: str
    first_published_at: datetime | None
