"""add billing idempotency and checkout attempt foundation

Revision ID: d83f6a1c9b42
Revises: b74f19c2e6a1
Create Date: 2026-11-04 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d83f6a1c9b42"
down_revision: Union[str, Sequence[str], None] = "b74f19c2e6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ATTEMPT_STATES = "('creating', 'ready', 'indeterminate', 'failed', 'resolved')"


def upgrade() -> None:
    op.create_table(
        "checkout_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_order_id", sa.String(length=50), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fencing_generation",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN " + _ATTEMPT_STATES,
            name="ck_checkout_attempts_status",
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0 AND length(provider_order_id) > 0",
            name="ck_checkout_attempts_provider_identity",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_checkout_attempts_amount"),
        sa.CheckConstraint(
            "fencing_generation >= 1",
            name="ck_checkout_attempts_generation",
        ),
        sa.CheckConstraint("attempt_count >= 1", name="ck_checkout_attempts_attempt_count"),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_checkout_attempts_lease_pair",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_checkout_attempts_payment_id_payments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_checkout_attempts_payment_id"),
        sa.UniqueConstraint(
            "provider",
            "provider_order_id",
            name="uq_checkout_attempts_provider_order_id",
        ),
    )
    op.create_index(
        "ix_checkout_attempts_recovery",
        "checkout_attempts",
        ["status", "next_reconcile_at", "lease_until"],
    )

    op.create_table(
        "checkout_attempt_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.String(length=20), nullable=True),
        sa.Column("to_state", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("fencing_generation", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_state IS NULL OR from_state IN " + _ATTEMPT_STATES,
            name="ck_checkout_attempt_history_from_state",
        ),
        sa.CheckConstraint(
            "to_state IN " + _ATTEMPT_STATES,
            name="ck_checkout_attempt_history_to_state",
        ),
        sa.CheckConstraint(
            "event_type IN "
            "('created', 'lease_acquired', 'lease_expired', 'state_changed', "
            "'lease_released')",
            name="ck_checkout_attempt_history_event_type",
        ),
        sa.CheckConstraint(
            "fencing_generation >= 1",
            name="ck_checkout_attempt_history_generation",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["checkout_attempts.id"],
            name="fk_checkout_attempt_history_attempt_id_checkout_attempts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkout_attempt_history_attempt_created",
        "checkout_attempt_history",
        ["attempt_id", "created_at"],
    )

    op.create_table(
        "billing_idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="in_progress",
            nullable=False,
        ),
        sa.Column("personal_purchase_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("checkout_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation IN ('personal_purchase', 'workspace_subscription', 'checkout')",
            name="ck_billing_idempotency_operation",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed')",
            name="ck_billing_idempotency_status",
        ),
        sa.CheckConstraint(
            "(status = 'in_progress' AND personal_purchase_id IS NULL "
            "AND subscription_id IS NULL AND payment_id IS NULL "
            "AND checkout_attempt_id IS NULL) OR "
            "(status = 'completed' AND "
            "((personal_purchase_id IS NOT NULL AND payment_id IS NOT NULL "
            "AND subscription_id IS NULL AND checkout_attempt_id IS NULL) OR "
            "(subscription_id IS NOT NULL AND payment_id IS NOT NULL "
            "AND personal_purchase_id IS NULL AND checkout_attempt_id IS NULL) OR "
            "(checkout_attempt_id IS NOT NULL AND personal_purchase_id IS NULL "
            "AND subscription_id IS NULL AND payment_id IS NULL)))",
            name="ck_billing_idempotency_result",
        ),
        sa.CheckConstraint("replay_count >= 0", name="ck_billing_idempotency_replay_count"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_billing_idempotency_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["personal_purchase_id"],
            ["personal_purchases.id"],
            name="fk_billing_idempotency_personal_purchase_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name="fk_billing_idempotency_subscription_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_billing_idempotency_payment_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checkout_attempt_id"],
            ["checkout_attempts.id"],
            name="fk_billing_idempotency_checkout_attempt_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "operation",
            "key_hash",
            name="uq_billing_idempotency_user_operation_key",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    protected_rows = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM billing_idempotency_records) + "
            "(SELECT count(*) FROM checkout_attempt_history) + "
            "(SELECT count(*) FROM checkout_attempts) + "
            "(SELECT count(*) FROM payments "
            " WHERE provider_order_id IS NOT NULL)"
        )
    ).scalar_one()
    if protected_rows:
        raise RuntimeError(
            "Cannot downgrade checkout idempotency foundation while idempotency, "
            "checkout history, checkout attempts, or provider order IDs exist."
        )

    op.drop_table("billing_idempotency_records")
    op.drop_index(
        "ix_checkout_attempt_history_attempt_created",
        table_name="checkout_attempt_history",
    )
    op.drop_table("checkout_attempt_history")
    op.drop_index("ix_checkout_attempts_recovery", table_name="checkout_attempts")
    op.drop_table("checkout_attempts")
