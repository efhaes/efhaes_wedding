from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Uuid,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, TimestampMixin, UUIDPrimaryKey


class EntitlementOverride(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "entitlement_overrides"
    __table_args__ = (
        CheckConstraint(
            "(invitation_id IS NOT NULL AND workspace_id IS NULL) OR "
            "(invitation_id IS NULL AND workspace_id IS NOT NULL)",
            name="ck_entitlement_overrides_exactly_one_scope",
        ),
        Index("ix_entitlement_overrides_invitation_feature", "invitation_id", "feature_id"),
        Index("ix_entitlement_overrides_workspace_feature", "workspace_id", "feature_id"),
        Index("ix_entitlement_overrides_feature_id", "feature_id"),
    )

    invitation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("invitations.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    feature_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("features.id", ondelete="RESTRICT"), nullable=False
    )
    is_enabled: Mapped[bool | None] = mapped_column()
    limit_override: Mapped[JSONValue | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
