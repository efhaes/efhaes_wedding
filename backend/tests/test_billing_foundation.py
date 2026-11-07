import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Index, Integer, UniqueConstraint, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.billing_idempotency_record import BillingIdempotencyRecord
from app.models.checkout_attempt import CheckoutAttempt, CheckoutAttemptHistory
from app.models.payment import Payment
from app.models.payment_webhook_event import PaymentWebhookEvent
from app.models.subscription import Subscription
from app.models.workspace import Workspace
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
    VerifiedPaymentOutcome,
)
from app.modules.billing.services.checkout_foundation_service import (
    build_provider_order_id,
)
from app.modules.billing.services.idempotency import (
    claim_idempotency_record,
    complete_idempotency_record,
    hash_idempotency_key,
    request_fingerprint,
)
from app.modules.billing.services.provider_event_identity import (
    build_provider_event_id,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "sqlite://",
        "JWT_SECRET_KEY": "test-only-secret-with-at-least-thirty-two-characters",
        "MIDTRANS_SERVER_KEY": None,
        "MIDTRANS_CLIENT_KEY": None,
        "MIDTRANS_IS_PRODUCTION": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_midtrans_configuration_defaults_to_sandbox_and_selects_production():
    sandbox = _settings()
    production = _settings(MIDTRANS_IS_PRODUCTION=True)

    assert sandbox.midtrans_api_base_url == "https://api.sandbox.midtrans.com"
    assert production.midtrans_api_base_url == "https://api.midtrans.com"


def test_midtrans_keys_are_validated_only_when_required():
    settings = _settings(MIDTRANS_SERVER_KEY="")

    with pytest.raises(RuntimeError, match="MIDTRANS_SERVER_KEY"):
        settings.require_midtrans_server_key()

    configured = _settings(MIDTRANS_SERVER_KEY="test-server-key")
    assert configured.require_midtrans_server_key().get_secret_value() == (
        "test-server-key"
    )
    assert str(configured.MIDTRANS_SERVER_KEY) == "**********"
    with pytest.raises(RuntimeError, match="MIDTRANS_CLIENT_KEY"):
        configured.require_midtrans_client_key()


def test_provider_identity_is_nullable_and_unique_within_provider():
    payment_columns = Payment.__table__.c
    event_columns = PaymentWebhookEvent.__table__.c

    assert payment_columns.provider.nullable
    assert payment_columns.provider_order_id.nullable
    assert payment_columns.provider_transaction_id.nullable
    assert event_columns.provider.nullable
    assert not event_columns.provider_event_id.unique
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("provider", "provider_event_id")
        for constraint in PaymentWebhookEvent.__table__.constraints
    )
    legacy_event_index = next(
        index
        for index in PaymentWebhookEvent.__table__.indexes
        if index.name == "uq_payment_webhook_events_legacy_event_id"
    )
    assert isinstance(legacy_event_index, Index)
    assert legacy_event_index.unique
    assert tuple(column.name for column in legacy_event_index.columns) == (
        "provider_event_id",
    )
    assert str(legacy_event_index.dialect_options["postgresql"]["where"]) == (
        "provider IS NULL"
    )
    assert {
        index.name for index in Payment.__table__.indexes if index.unique
    } >= {
        "uq_payments_provider_order_id",
        "uq_payments_provider_transaction_id",
    }


def test_webhook_processing_attempts_model_and_migration_types_match():
    column = PaymentWebhookEvent.__table__.c.processing_attempts
    assert isinstance(column.type, Integer)
    assert column.nullable is False
    assert column.default.arg == 0
    assert column.server_default.arg == "0"

    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "b74f19c2e6a1_midtrans_payment_foundation.py"
    )
    migration = ast.parse(migration_path.read_text(encoding="utf-8"))
    migration_strings = {
        node.value
        for node in ast.walk(migration)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "uq_payment_webhook_events_legacy_event_id" in migration_strings
    assert "provider IS NULL" in migration_strings
    assert "a6b75c4d2e90" in migration_strings
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Integer"
        for node in ast.walk(migration)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SmallInteger"
        for node in ast.walk(migration)
    )


def test_checkout_and_idempotency_model_constraints_are_present():
    idempotency_constraints = BillingIdempotencyRecord.__table__.constraints
    attempt_constraints = CheckoutAttempt.__table__.constraints
    history_constraints = CheckoutAttemptHistory.__table__.constraints

    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("user_id", "operation", "key_hash")
        for constraint in idempotency_constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("payment_id",)
        for constraint in attempt_constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("provider", "provider_order_id")
        for constraint in attempt_constraints
    )
    assert CheckoutAttempt.__table__.c.provider_order_id.type.length == 50
    assert CheckoutAttempt.__table__.c.lease_owner.nullable
    assert CheckoutAttempt.__table__.c.lease_until.nullable
    assert not CheckoutAttempt.__table__.c.fencing_generation.nullable
    assert "ix_checkout_attempts_recovery" in {
        index.name for index in CheckoutAttempt.__table__.indexes
    }
    assert "ix_checkout_attempt_history_attempt_created" in {
        index.name for index in CheckoutAttemptHistory.__table__.indexes
    }
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for table in (
            BillingIdempotencyRecord.__table__,
            CheckoutAttempt.__table__,
            CheckoutAttemptHistory.__table__,
        )
        for column in table.columns
        for foreign_key in column.foreign_keys
    )


def test_idempotency_key_hash_and_fingerprint_are_deterministic():
    key = "client-generated-idempotency-key-001"

    assert hash_idempotency_key(key) == hash_idempotency_key(key)
    assert key not in hash_idempotency_key(key)
    assert request_fingerprint(
        "personal_purchase", {"plan_id": "plan-1", "user_id": "user-1"}
    ) == request_fingerprint(
        "personal_purchase", {"user_id": "user-1", "plan_id": "plan-1"}
    )
    assert request_fingerprint(
        "personal_purchase", {"plan_id": "plan-1"}
    ) != request_fingerprint("personal_purchase", {"plan_id": "plan-2"})


def test_concurrent_idempotency_claim_has_one_sqlite_winner(tmp_path):
    database_path = tmp_path / "idempotency-race.sqlite"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    BillingIdempotencyRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)
    user_id = uuid4()
    key_hash = hash_idempotency_key("concurrent-idempotency-key-001")
    fingerprint = request_fingerprint("personal_purchase", {"plan_id": "plan-1"})

    def claim():
        with factory() as session:
            barrier.wait(timeout=10)
            try:
                record, inserted = claim_idempotency_record(
                    session,
                    user_id=user_id,
                    operation="personal_purchase",
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                )
                if inserted:
                    complete_idempotency_record(
                        record,
                        personal_purchase_id=uuid4(),
                        payment_id=uuid4(),
                    )
                session.commit()
                return inserted
            except HTTPException as error:
                session.rollback()
                assert error.status_code == 409
                return False

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: claim(), range(2)))

        assert sorted(results) == [False, True]
    finally:
        BillingIdempotencyRecord.__table__.drop(engine)
        engine.dispose()


def test_provider_order_id_is_stable_and_within_midtrans_limit():
    payment_id = uuid4()
    order_id = build_provider_order_id(payment_id)

    assert order_id == build_provider_order_id(payment_id)
    assert len(order_id) <= 50
    assert order_id.startswith("pay-")
    assert all(
        (character.isascii() and character.isalnum()) or character in "-._~"
        for character in order_id
    )


def test_checkout_migration_revision_and_parent():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "d83f6a1c9b42_checkout_idempotency_foundation.py"
    )
    migration = ast.parse(migration_path.read_text(encoding="utf-8"))
    revision_values = {
        node.target.id: node.value.value
        for node in ast.walk(migration)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
    }

    assert revision_values["revision"] == "d83f6a1c9b42"
    assert revision_values["down_revision"] == "b74f19c2e6a1"
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"upgrade", "downgrade", "stamp"}
        for node in ast.walk(migration)
    )


def test_midtrans_migration_revisions_are_unique_and_legacy_index_is_reversible():
    versions_path = Path(__file__).parents[1] / "alembic" / "versions"
    revisions: dict[str, str | None] = {}
    for migration_path in versions_path.glob("*.py"):
        migration = ast.parse(migration_path.read_text(encoding="utf-8"))
        values = {
            node.target.id: node.value.value
            for node in ast.walk(migration)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        revision = values.get("revision")
        if revision is not None:
            assert revision not in revisions
            revisions[revision] = values.get("down_revision")
        if migration_path.name == "b74f19c2e6a1_midtrans_payment_foundation.py":
            strings = {
                node.value
                for node in ast.walk(migration)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            assert revision == "b74f19c2e6a1"
            assert values["down_revision"] == "a6b75c4d2e90"
            assert "uq_payment_webhook_events_legacy_event_id" in strings
            assert any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "drop_index"
                and any(
                    isinstance(argument, ast.Constant)
                    and argument.value == "uq_payment_webhook_events_legacy_event_id"
                    for argument in node.args
                )
                for node in ast.walk(migration)
            )

    assert revisions["d83f6a1c9b42"] == "b74f19c2e6a1"


def test_new_webhook_events_require_provider_but_legacy_provider_is_nullable():
    assert PaymentWebhookEvent.__table__.c.provider.nullable
    engine = create_engine("sqlite://")
    PaymentWebhookEvent.__table__.create(engine)
    try:
        with Session(engine) as session:
            session.add(
                PaymentWebhookEvent(
                    payment_id=uuid4(),
                    provider_event_id="legacy-row",
                    event_type="settlement",
                    payload={},
                )
            )
            with pytest.raises(ValueError, match="require a provider"):
                session.flush()
            session.rollback()

            valid_event = PaymentWebhookEvent(
                payment_id=uuid4(),
                provider=" MidTrans ",
                provider_event_id="event-1",
                event_type="settlement",
                payload={},
            )
            session.add(valid_event)
            session.flush()
            assert valid_event.provider == "midtrans"
    finally:
        PaymentWebhookEvent.__table__.drop(engine)
        engine.dispose()


def _notification(**overrides: object) -> dict[str, object]:
    notification: dict[str, object] = {
        "order_id": "ORDER-123",
        "transaction_id": "TX-456",
        "transaction_status": " settlement ",
        "status_code": "0200",
        "gross_amount": "100.00",
        "currency": " idr ",
        "payment_type": "bank_transfer",
        "transaction_time": "2026-11-04 09:00:00",
        "settlement_time": "2026-11-04 10:00:00",
        "signature_key": "must-not-affect-event-identity",
    }
    notification.update(overrides)
    return notification


def test_provider_event_identity_is_deterministic_and_key_order_independent():
    notification = _notification()
    reversed_notification = dict(reversed(list(notification.items())))

    assert build_provider_event_id("midtrans", notification) == (
        build_provider_event_id("midtrans", notification)
    )
    assert build_provider_event_id("Midtrans", notification) == (
        build_provider_event_id(" midtrans ", reversed_notification)
    )


def test_provider_event_identity_ignores_notification_timestamps():
    base = _notification()
    retry = _notification(
        transaction_time="2026-11-04 09:01:00",
        settlement_time="2026-11-04 10:01:00",
        expiry_time="2026-11-05 10:00:00",
        cancel_time="2026-11-04 10:02:00",
    )
    assert build_provider_event_id("midtrans", base) == build_provider_event_id(
        "midtrans", retry
    )


@pytest.mark.parametrize("amount", [100, 100.0, "100.00", "100.000"])
def test_provider_event_identity_normalizes_decimal_amount(amount):
    assert build_provider_event_id(
        "midtrans", _notification(gross_amount=amount)
    ) == build_provider_event_id("midtrans", _notification(gross_amount="100"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_status", "expire"),
        ("status_code", "201"),
        ("order_id", "ORDER-124"),
        ("transaction_id", "TX-457"),
    ],
)
def test_provider_event_identity_distinguishes_identity_changes(field, value):
    base = _notification()

    assert build_provider_event_id("midtrans", base) != build_provider_event_id(
        "midtrans", _notification(**{field: value})
    )


def test_provider_event_identity_distinguishes_provider_and_normalizes_fields():
    base = _notification()
    assert build_provider_event_id("midtrans", base) != build_provider_event_id(
        "other_provider", base
    )
    assert build_provider_event_id("midtrans", base) == build_provider_event_id(
        " MIDTRANS ",
        _notification(
            transaction_status="SETTLEMENT",
            status_code=200,
            gross_amount=100.0,
            currency="IDR",
            order_id=" ORDER-123 ",
            transaction_id=" TX-456 ",
        ),
    )
    assert build_provider_event_id("midtrans", base) == build_provider_event_id(
        "midtrans", _notification(signature_key="different-secret-value")
    )


@pytest.mark.parametrize("field", ["order_id", "transaction_id"])
def test_provider_event_identity_rejects_missing_transaction_identifiers(field):
    notification = _notification()
    del notification[field]
    with pytest.raises(ValueError, match=f"{field} is required"):
        build_provider_event_id("midtrans", notification)


@pytest.mark.parametrize("amount", ["not-an-amount", "-1", "NaN", "100.001"])
def test_provider_event_identity_rejects_invalid_amount(amount):
    with pytest.raises(ValueError, match="gross_amount"):
        build_provider_event_id("midtrans", _notification(gross_amount=amount))


def test_provider_event_identity_requires_valid_currency_and_status_code():
    with pytest.raises(ValueError, match="currency"):
        build_provider_event_id("midtrans", _notification(currency="US"))
    with pytest.raises(ValueError, match="status_code"):
        build_provider_event_id("midtrans", _notification(status_code="20x"))


def test_subscription_outcome_locks_workspace_before_payment_and_subscription():
    workspace_id = uuid4()
    payment = Payment(
        id=uuid4(),
        subscription_id=uuid4(),
        status="pending",
    )
    workspace = Workspace(id=workspace_id, status="active")
    subscription = Subscription(
        id=payment.subscription_id,
        workspace_id=workspace_id,
        status="pending",
    )
    lock_order: list[str] = []

    class RepositoryStub:
        def get_payment(self, _payment_id):
            return payment

        def subscription_workspace_id(self, _subscription_id):
            return workspace_id

        def lock_workspace(self, requested_workspace_id):
            assert requested_workspace_id == workspace_id
            lock_order.append("workspace")
            return workspace

        def lock_payment(self, _payment_id):
            lock_order.append("payment")
            return payment

        def lock_subscription(self, _subscription_id):
            lock_order.append("subscription")
            return subscription

        def checkout_attempt_for_payment(self, _payment_id, *, lock=False):
            return None

    class SessionStub:
        def commit(self):
            lock_order.append("commit")

    service = PaymentLifecycleService(SessionStub())
    service.repository = RepositoryStub()

    service.record_verified_outcome(
        payment.id,
        VerifiedPaymentOutcome(status="failed"),
    )

    assert lock_order == ["workspace", "payment", "subscription", "commit"]
