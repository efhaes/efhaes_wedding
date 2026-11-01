from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, JSONValue, UUIDPrimaryKey


class PlanFeature(UUIDPrimaryKey, Base):
    __tablename__ = "plan_features"
    __table_args__ = (
        UniqueConstraint("plan_id", "feature_id", name="uq_plan_features_plan_feature"),
        Index("ix_plan_features_feature_id", "feature_id"),
    )

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    feature_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("features.id", ondelete="RESTRICT"), nullable=False
    )
    configuration: Mapped[JSONValue] = mapped_column(JSONB, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
