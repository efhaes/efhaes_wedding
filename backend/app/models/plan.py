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
        CheckConstraint(
            "price IS NULL OR price >= 0", name="ck_plans_price_nonnegative"
        ),
        CheckConstraint(
            "(price IS NULL AND currency IS NULL) OR "
            "(price IS NOT NULL AND price >= 0 AND "
            " ((price = 0 AND (currency IS NULL OR length(currency) = 3)) OR "
            "  (price > 0 AND currency IS NOT NULL AND length(currency) = 3)))",
            name="ck_plans_price_currency_pair",
        ),
        CheckConstraint(
            "(billing_type = 'one_time' AND "
            " ((duration_type = 'pending' AND duration_days IS NULL) OR "
            "  (duration_type = 'fixed' AND duration_days IS NOT NULL "
            "   AND duration_days > 0) OR "
            "  (duration_type = 'lifetime' AND duration_days IS NULL))) OR "
            "(billing_type = 'subscription' AND "
            " duration_type = 'recurring' AND duration_days IS NULL)",
            name="ck_plans_duration_configuration",
        ),
        CheckConstraint(
            "billing_period_days IS NULL OR "
            "(billing_type = 'subscription' AND billing_period_days > 0)",
            name="ck_plans_billing_period_days",
        ),
        CheckConstraint(
            "duration_type IN ('pending', 'fixed', 'lifetime', 'recurring')",
            name="ck_plans_duration_type",
        ),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    billing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    duration_days: Mapped[int | None] = mapped_column()
    billing_period_days: Mapped[int | None] = mapped_column()
    duration_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
