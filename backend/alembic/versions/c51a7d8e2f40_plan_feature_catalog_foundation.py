"""add explicit plan duration and catalog configuration

Revision ID: c51a7d8e2f40
Revises: f2c8a9d0137e
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c51a7d8e2f40"
down_revision: Union[str, Sequence[str], None] = "f2c8a9d0137e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    ambiguous_personal_plans = bind.execute(
        sa.text(
            "SELECT count(*) FROM plans "
            "WHERE billing_type = 'one_time' AND duration_days IS NULL"
        )
    ).scalar_one()
    subscription_plans_with_duration = bind.execute(
        sa.text(
            "SELECT count(*) FROM plans "
            "WHERE billing_type = 'subscription' AND duration_days IS NOT NULL"
        )
    ).scalar_one()
    if ambiguous_personal_plans or subscription_plans_with_duration:
        raise RuntimeError(
            "Cannot infer legacy plan duration semantics safely; "
            f"one-time NULL-duration plans={ambiguous_personal_plans}, "
            "subscription plans with duration_days="
            f"{subscription_plans_with_duration}. Configure these plans explicitly "
            "before upgrading."
        )

    op.add_column(
        "plans",
        sa.Column("duration_type", sa.String(length=20), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE plans SET duration_type = CASE "
            "WHEN billing_type = 'subscription' THEN 'recurring' "
            "WHEN billing_type = 'one_time' THEN 'fixed' END"
        )
    )
    op.alter_column("plans", "duration_type", nullable=False)
    op.alter_column(
        "plans",
        "price",
        existing_type=sa.Numeric(12, 2),
        nullable=True,
    )
    op.alter_column(
        "plans",
        "currency",
        existing_type=sa.String(length=3),
        nullable=True,
    )
    op.drop_constraint("ck_plans_duration_positive", "plans", type_="check")
    op.drop_constraint("ck_plans_price_nonnegative", "plans", type_="check")
    op.create_check_constraint(
        "ck_plans_price_nonnegative",
        "plans",
        "price IS NULL OR price >= 0",
    )
    op.create_check_constraint(
        "ck_plans_price_currency_pair",
        "plans",
        "(price IS NULL AND currency IS NULL) OR "
        "(price IS NOT NULL AND price >= 0 AND "
        " ((price = 0 AND (currency IS NULL OR length(currency) = 3)) OR "
        "  (price > 0 AND currency IS NOT NULL AND length(currency) = 3)))",
    )
    op.create_check_constraint(
        "ck_plans_duration_type",
        "plans",
        "duration_type IN ('pending', 'fixed', 'lifetime', 'recurring')",
    )
    op.create_check_constraint(
        "ck_plans_duration_configuration",
        "plans",
        "(billing_type = 'one_time' AND "
        " ((duration_type = 'pending' AND duration_days IS NULL) OR "
        "  (duration_type = 'fixed' AND duration_days IS NOT NULL "
        "   AND duration_days > 0) OR "
        "  (duration_type = 'lifetime' AND duration_days IS NULL))) OR "
        "(billing_type = 'subscription' AND "
        " duration_type = 'recurring' AND duration_days IS NULL)",
    )

    op.add_column(
        "features",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "plan_features",
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    op.add_column(
        "personal_purchases",
        sa.Column("duration_type_snapshot", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "personal_purchases",
        sa.Column("duration_days_snapshot", sa.Integer(), nullable=True),
    )
    op.add_column(
        "personal_purchases",
        sa.Column("purchased_price", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "personal_purchases",
        sa.Column("purchased_currency", sa.String(length=3), nullable=True),
    )
    op.create_check_constraint(
        "ck_personal_purchases_duration_snapshot",
        "personal_purchases",
        "(duration_type_snapshot IS NULL AND duration_days_snapshot IS NULL) OR "
        "(duration_type_snapshot = 'fixed' AND "
        " duration_days_snapshot IS NOT NULL AND duration_days_snapshot > 0) OR "
        "(duration_type_snapshot = 'lifetime' AND duration_days_snapshot IS NULL)",
    )
    op.create_check_constraint(
        "ck_personal_purchases_price_snapshot",
        "personal_purchases",
        "(purchased_price IS NULL AND purchased_currency IS NULL) OR "
        "(purchased_price IS NOT NULL AND purchased_price >= 0 AND "
        " ((purchased_price = 0 AND "
        "   (purchased_currency IS NULL OR length(purchased_currency) = 3)) OR "
        "  (purchased_price > 0 AND purchased_currency IS NOT NULL "
        "   AND length(purchased_currency) = 3)))",
    )


def downgrade() -> None:
    bind = op.get_bind()
    non_downgradable_plans = bind.execute(
        sa.text(
            "SELECT count(*) FROM plans "
            "WHERE duration_type IN ('lifetime', 'pending') "
            "OR price IS NULL OR currency IS NULL"
        )
    ).scalar_one()
    populated_purchase_snapshots = bind.execute(
        sa.text(
            "SELECT count(*) FROM personal_purchases "
            "WHERE duration_type_snapshot IS NOT NULL "
            "OR duration_days_snapshot IS NOT NULL "
            "OR purchased_price IS NOT NULL OR purchased_currency IS NOT NULL"
        )
    ).scalar_one()
    inactive_catalog_entries = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM features WHERE is_active IS NOT TRUE) + "
            "(SELECT count(*) FROM plan_features WHERE is_enabled IS NOT TRUE)"
        )
    ).scalar_one()
    if non_downgradable_plans or populated_purchase_snapshots or inactive_catalog_entries:
        raise RuntimeError(
            "Cannot downgrade without losing catalog or purchase snapshot data; "
            f"plans={non_downgradable_plans}, "
            f"purchase snapshots={populated_purchase_snapshots}, "
            f"inactive catalog entries={inactive_catalog_entries}."
        )

    op.drop_constraint(
        "ck_personal_purchases_price_snapshot",
        "personal_purchases",
        type_="check",
    )
    op.drop_constraint(
        "ck_personal_purchases_duration_snapshot",
        "personal_purchases",
        type_="check",
    )
    op.drop_column("personal_purchases", "purchased_currency")
    op.drop_column("personal_purchases", "purchased_price")
    op.drop_column("personal_purchases", "duration_days_snapshot")
    op.drop_column("personal_purchases", "duration_type_snapshot")

    op.drop_column("plan_features", "is_enabled")
    op.drop_column("features", "is_active")

    op.drop_constraint("ck_plans_duration_configuration", "plans", type_="check")
    op.drop_constraint("ck_plans_duration_type", "plans", type_="check")
    op.drop_constraint("ck_plans_price_currency_pair", "plans", type_="check")
    op.drop_constraint("ck_plans_price_nonnegative", "plans", type_="check")
    op.create_check_constraint(
        "ck_plans_duration_positive",
        "plans",
        "duration_days IS NULL OR duration_days > 0",
    )
    op.create_check_constraint(
        "ck_plans_price_nonnegative",
        "plans",
        "price >= 0",
    )
    op.alter_column(
        "plans",
        "currency",
        existing_type=sa.String(length=3),
        nullable=False,
    )
    op.alter_column(
        "plans",
        "price",
        existing_type=sa.Numeric(12, 2),
        nullable=False,
    )
    op.drop_column("plans", "duration_type")
