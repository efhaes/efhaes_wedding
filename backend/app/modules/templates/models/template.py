from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, TimestampMixin, UUIDPrimaryKey


class Template(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint(
            "access_level IN ('free', 'premium')", name="ck_templates_access_level"
        ),
        CheckConstraint("version > 0", name="ck_templates_version_positive"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_templates_status",
        ),
        Index("ix_templates_category_active", "category", "is_active"),
        Index("ix_templates_status_category", "status", "category"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    access_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="free", server_default="free"
    )
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    preview_image_url: Mapped[str | None] = mapped_column(String(2048))
    configuration: Mapped[JSONValue] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
