"""add purchase payment lifecycle snapshots

Revision ID: a6b75c4d2e90
Revises: d13f7a9c2b64
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a6b75c4d2e90"
down_revision: Union[str, Sequence[str], None] = "d13f7a9c2b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("billing_period_days", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_plans_billing_period_days",
        "plans",
        "billing_period_days IS NULL OR "
        "(billing_type = 'subscription' AND billing_period_days > 0)",
    )

    op.add_column(
        "personal_purchases",
        sa.Column(
            "entitlement_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.drop_constraint("ck_subscriptions_period_order", "subscriptions", type_="check")
    op.drop_constraint("ck_subscriptions_status", "subscriptions", type_="check")
    op.create_check_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        "status IN ('pending', 'trialing', 'active', 'past_due', "
        "'cancelled', 'expired', 'refunded')",
    )
    op.create_check_constraint(
        "ck_subscriptions_period_order",
        "subscriptions",
        "(status IN ('pending', 'cancelled', 'expired', 'refunded') "
        "AND current_period_start IS NULL AND current_period_end IS NULL) OR "
        "(current_period_start IS NOT NULL AND current_period_end IS NOT NULL "
        "AND current_period_end > current_period_start)",
    )
    op.alter_column(
        "subscriptions",
        "current_period_start",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "subscriptions",
        "current_period_end",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.add_column(
        "subscriptions",
        sa.Column("billing_period_days_snapshot", sa.Integer(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("purchased_price", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("purchased_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "entitlement_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_subscriptions_billing_period_snapshot",
        "subscriptions",
        "billing_period_days_snapshot IS NULL OR billing_period_days_snapshot > 0",
    )
    op.create_check_constraint(
        "ck_subscriptions_price_snapshot",
        "subscriptions",
        "(purchased_price IS NULL AND purchased_currency IS NULL) OR "
        "(purchased_price IS NOT NULL AND purchased_price >= 0 AND "
        " ((purchased_price = 0 AND "
        "   (purchased_currency IS NULL OR length(purchased_currency) = 3)) OR "
        "  (purchased_price > 0 AND purchased_currency IS NOT NULL "
        "   AND length(purchased_currency) = 3)))",
    )

    op.alter_column(
        "payments",
        "provider",
        existing_type=sa.String(length=80),
        nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    unsafe_rows = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM plans WHERE billing_period_days IS NOT NULL) + "
            "(SELECT count(*) FROM personal_purchases "
            " WHERE entitlement_snapshot IS NOT NULL) + "
            "(SELECT count(*) FROM subscriptions WHERE "
            " entitlement_snapshot IS NOT NULL OR "
            " billing_period_days_snapshot IS NOT NULL OR "
            " purchased_price IS NOT NULL OR purchased_currency IS NOT NULL OR "
            " current_period_start IS NULL OR current_period_end IS NULL OR "
            " status = 'refunded') + "
            "(SELECT count(*) FROM payments WHERE provider IS NULL)"
        )
    ).scalar_one()
    if unsafe_rows:
        raise RuntimeError(
            "Cannot downgrade purchase/payment lifecycle migration without losing "
            f"configuration, snapshots, pending subscriptions, or payment provider data "
            f"({unsafe_rows} affected rows)."
        )

    op.alter_column(
        "payments",
        "provider",
        existing_type=sa.String(length=80),
        nullable=False,
    )
    op.drop_constraint(
        "ck_subscriptions_price_snapshot", "subscriptions", type_="check"
    )
    op.drop_constraint(
        "ck_subscriptions_billing_period_snapshot",
        "subscriptions",
        type_="check",
    )
    op.drop_column("subscriptions", "entitlement_snapshot")
    op.drop_column("subscriptions", "purchased_currency")
    op.drop_column("subscriptions", "purchased_price")
    op.drop_column("subscriptions", "billing_period_days_snapshot")
    op.drop_constraint("ck_subscriptions_period_order", "subscriptions", type_="check")
    op.drop_constraint("ck_subscriptions_status", "subscriptions", type_="check")
    op.create_check_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        "status IN ('pending', 'trialing', 'active', 'past_due', 'cancelled', 'expired')",
    )
    op.alter_column(
        "subscriptions",
        "current_period_end",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "subscriptions",
        "current_period_start",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_subscriptions_period_order",
        "subscriptions",
        "current_period_end > current_period_start",
    )

    op.drop_column("personal_purchases", "entitlement_snapshot")
    op.drop_constraint("ck_plans_billing_period_days", "plans", type_="check")
    op.drop_column("plans", "billing_period_days")
