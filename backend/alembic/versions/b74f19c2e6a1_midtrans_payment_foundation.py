"""add provider payment and webhook foundations

Revision ID: b74f19c2e6a1
Revises: a6b75c4d2e90
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b74f19c2e6a1"
down_revision: Union[str, Sequence[str], None] = "a6b75c4d2e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("provider_order_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("provider_transaction_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_payments_provider_order_id",
        "payments",
        ["provider", "provider_order_id"],
        unique=True,
        postgresql_where=sa.text(
            "provider IS NOT NULL AND provider_order_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_payments_provider_transaction_id",
        "payments",
        ["provider", "provider_transaction_id"],
        unique=True,
        postgresql_where=sa.text(
            "provider IS NOT NULL AND provider_transaction_id IS NOT NULL"
        ),
    )

    op.add_column(
        "payment_webhook_events",
        sa.Column("provider", sa.String(length=80), nullable=True),
    )
    op.drop_constraint(
        "payment_webhook_events_provider_event_id_key",
        "payment_webhook_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_payment_webhook_events_provider_event_id",
        "payment_webhook_events",
        ["provider", "provider_event_id"],
    )
    op.create_index(
        "uq_payment_webhook_events_legacy_event_id",
        "payment_webhook_events",
        ["provider_event_id"],
        unique=True,
        postgresql_where=sa.text("provider IS NULL"),
    )
    op.create_check_constraint(
        "ck_payment_webhook_events_provider_nonempty",
        "payment_webhook_events",
        "provider IS NULL OR length(trim(provider)) > 0",
    )
    op.add_column(
        "payment_webhook_events",
        sa.Column(
            "processing_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "payment_webhook_events",
        sa.Column(
            "processing_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE payment_webhook_events "
            "SET processing_status = 'processed' WHERE processed_at IS NOT NULL"
        )
    )
    op.create_check_constraint(
        "ck_payment_webhook_events_processing_status",
        "payment_webhook_events",
        "processing_status IN ('pending', 'processed', 'failed')",
    )
    op.create_check_constraint(
        "ck_payment_webhook_events_processing_attempts",
        "payment_webhook_events",
        "processing_attempts >= 0",
    )


def downgrade() -> None:
    bind = op.get_bind()
    unsafe_rows = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM payments WHERE "
            " provider_order_id IS NOT NULL OR provider_transaction_id IS NOT NULL) + "
            "(SELECT count(*) FROM payment_webhook_events WHERE "
            " provider IS NOT NULL OR "
            " processing_status != CASE WHEN processed_at IS NOT NULL "
            " THEN 'processed' ELSE 'pending' END OR "
            " processing_attempts != 0) + "
            "(SELECT count(*) FROM (SELECT provider_event_id "
            " FROM payment_webhook_events GROUP BY provider_event_id "
            " HAVING count(*) > 1) duplicate_events)"
        )
    ).scalar_one()
    if unsafe_rows:
        raise RuntimeError(
            "Cannot downgrade Midtrans payment foundation without losing provider "
            "identifiers, webhook processing state, or provider-scoped event identity."
        )

    op.drop_constraint(
        "ck_payment_webhook_events_processing_attempts",
        "payment_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_webhook_events_processing_status",
        "payment_webhook_events",
        type_="check",
    )
    op.drop_column("payment_webhook_events", "processing_attempts")
    op.drop_column("payment_webhook_events", "processing_status")
    op.drop_constraint(
        "ck_payment_webhook_events_provider_nonempty",
        "payment_webhook_events",
        type_="check",
    )
    op.drop_index(
        "uq_payment_webhook_events_legacy_event_id",
        table_name="payment_webhook_events",
    )
    op.drop_constraint(
        "uq_payment_webhook_events_provider_event_id",
        "payment_webhook_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "payment_webhook_events_provider_event_id_key",
        "payment_webhook_events",
        ["provider_event_id"],
    )
    op.drop_column("payment_webhook_events", "provider")

    op.drop_index("uq_payments_provider_transaction_id", table_name="payments")
    op.drop_index("uq_payments_provider_order_id", table_name="payments")
    op.drop_column("payments", "provider_transaction_id")
    op.drop_column("payments", "provider_order_id")
