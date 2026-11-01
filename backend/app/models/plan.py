from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class Plan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "billing_type IN ('one_time', 'subscription')", name="ck_plans_billing_type"
        ),
        CheckConstraint("price >= 0", name="ck_plans_price_nonnegative"),
        CheckConstraint(
            "duration_days IS NULL OR duration_days > 0", name="ck_plans_duration_positive"
        ),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    billing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    duration_days: Mapped[int | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
