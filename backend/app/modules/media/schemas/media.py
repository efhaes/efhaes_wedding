from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MediaRead(BaseModel):
    id: UUID
    original_filename: str
    mime_type: str
    file_size: int
    created_at: datetime
    file_url: str
