import base64
import hashlib
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import Text

from app.core.config import Settings
from app.models.checkout_attempt import CheckoutAttempt
from app.modules.billing.services.midtrans_client import (
    MidtransMalformedResponse,
    MidtransSnapClient,
    MidtransTimeout,
)
from app.modules.billing.services.midtrans_security import (
    decrypt_checkout_response,
    encrypt_checkout_response,
    verify_notification_signature,
)
from app.modules.billing.services.midtrans_verification_service import (
    _map_transaction_status,
)
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
    VerifiedPaymentOutcome,
    _issue_verified_payment_outcome,
)
from app.modules.billing.services.provider_event_identity import (
    build_provider_event_id,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "postgresql://unused/efhaeswedding_test",
        "JWT_SECRET_KEY": "test-secret-with-more-than-thirty-two-characters",
        "MIDTRANS_SERVER_KEY": "unit-server-key",
        "MIDTRANS_TOKEN_ENCRYPTION_KEY": "unit-encryption-key-with-at-least-32-chars",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_midtrans_configuration_validation_is_deferred_until_use():
    settings = _settings(MIDTRANS_CURRENCY="invalid")

    with pytest.raises(RuntimeError, match="MIDTRANS_CURRENCY"):
        settings.require_midtrans_currency()


def test_snap_request_uses_internal_sandbox_and_basic_auth():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "token": "snap-token",
                "redirect_url": (
                    "https://app.sandbox.midtrans.com/snap/v2/vtweb/snap-token"
                ),
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        result = MidtransSnapClient(
            _settings(), client=http_client
        ).create_transaction(
            order_id="pay-123",
            gross_amount=Decimal("125000"),
        )

    assert captured["url"] == (
        "https://app.sandbox.midtrans.com/snap/v1/transactions"
    )
    expected_auth = base64.b64encode(b"unit-server-key:").decode("ascii")
    assert captured["auth"] == f"Basic {expected_auth}"
    assert b'"gross_amount":125000' in captured["body"]
    assert result.token == "snap-token"


def test_snap_request_uses_production_endpoint_only_when_configured():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://app.midtrans.com/snap/v1/transactions"
        )
        return httpx.Response(
            200,
            json={
                "token": "snap-token",
                "redirect_url": "https://app.midtrans.com/snap/v2/vtweb/snap-token",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        MidtransSnapClient(
            _settings(MIDTRANS_IS_PRODUCTION=True),
            client=http_client,
        ).create_transaction(
            order_id="pay-123",
            gross_amount=Decimal("125000"),
        )


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://app.sandbox.midtrans.com/snap/v2/vtweb/token",
        "https://attacker.example/snap/v2/vtweb/token",
        "https://app.sandbox.midtrans.com.evil.example/snap/v2/vtweb/token",
    ],
)
def test_snap_response_rejects_untrusted_redirect_hosts(redirect_url: str):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"token": "snap-token", "redirect_url": redirect_url}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(MidtransMalformedResponse):
            MidtransSnapClient(_settings(), client=http_client).create_transaction(
                order_id="pay-123",
                gross_amount=Decimal("125000"),
            )


def test_snap_timeout_is_explicit_and_never_looks_like_success():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(MidtransTimeout):
            MidtransSnapClient(_settings(), client=http_client).create_transaction(
                order_id="pay-123",
                gross_amount=Decimal("125000"),
            )


def test_status_lookup_uses_midtrans_api_and_transaction_id_when_available():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://api.sandbox.midtrans.com/v2/transaction-123/status"
        )
        return httpx.Response(
            200,
            json={
                "status_code": "200",
                "order_id": "pay-123",
                "transaction_id": "transaction-123",
                "transaction_status": "settlement",
                "gross_amount": "125000.00",
                "currency": "IDR",
                "fraud_status": "accept",
                "payment_type": "bank_transfer",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = MidtransSnapClient(
            _settings(), client=http_client
        ).get_transaction_status(
            order_id="pay-123",
            transaction_id="transaction-123",
        )

    assert result.transaction_status == "settlement"
    assert result.gross_amount == Decimal("125000.00")


def test_status_lookup_preserves_terminal_provider_status_codes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://api.sandbox.midtrans.com/v2/pay-123/status"
        )
        return httpx.Response(
            200,
            json={
                "status_code": "202",
                "order_id": "pay-123",
                "transaction_id": "transaction-123",
                "transaction_status": "deny",
                "gross_amount": "125000.00",
                "currency": "IDR",
                "fraud_status": "deny",
                "payment_type": "bank_transfer",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = MidtransSnapClient(
            _settings(), client=http_client
        ).get_transaction_status(order_id="pay-123")

    assert result.status_code == "202"
    assert result.transaction_status == "deny"


def test_notification_signature_uses_official_sha512_formula():
    payload = {
        "order_id": "pay-123",
        "status_code": "200",
        "gross_amount": "125000.00",
        "transaction_id": "provider-transaction-123",
        "transaction_status": "settlement",
        "currency": "IDR",
    }
    payload["signature_key"] = hashlib.sha512(
        b"pay-123200125000.00unit-server-key"
    ).hexdigest()

    notification = verify_notification_signature(payload, "unit-server-key")

    assert notification.order_id == "pay-123"
    assert notification.gross_amount == Decimal("125000.00")
    payload["gross_amount"] = "125000.01"
    with pytest.raises(ValueError, match="signature"):
        verify_notification_signature(payload, "unit-server-key")


def test_provider_event_identity_deduplicates_retries_not_status_changes():
    base = {
        "order_id": "pay-123",
        "transaction_id": "provider-transaction-123",
        "transaction_status": "pending",
        "status_code": "201",
        "gross_amount": "125000.00",
        "currency": "IDR",
    }
    duplicate = dict(base)
    settlement = {**base, "transaction_status": "settlement", "status_code": "200"}

    assert build_provider_event_id("midtrans", base) == build_provider_event_id(
        "midtrans", duplicate
    )
    assert build_provider_event_id("midtrans", base) != build_provider_event_id(
        "midtrans", settlement
    )


def test_lifecycle_rejects_unsealed_midtrans_payment_outcomes():
    outcome = VerifiedPaymentOutcome(
        status="paid",
        provider="midtrans",
        provider_order_id="pay-123",
        provider_transaction_id="transaction-123",
    )
    with pytest.raises(HTTPException, match="Trusted Midtrans verification"):
        PaymentLifecycleService._validate_outcome(outcome)

    verified = _issue_verified_payment_outcome(
        "paid",
        paid_at=None,
        provider="midtrans",
        provider_order_id="pay-123",
        provider_transaction_id="transaction-123",
    )
    with pytest.raises(HTTPException, match="timezone-aware paid_at"):
        PaymentLifecycleService._validate_outcome(verified)


def test_encrypted_response_model_matches_unapplied_migration():
    column = CheckoutAttempt.__table__.c.provider_response_ciphertext
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "e4b7c9a13f62_encrypt_snap_checkout_response.py"
    )
    migration = migration_path.read_text(encoding="utf-8")

    assert isinstance(column.type, Text)
    assert column.nullable is True
    assert 'revision: str = "e4b7c9a13f62"' in migration
    assert 'down_revision: Union[str, Sequence[str], None] = "d83f6a1c9b42"' in migration
    assert 'sa.Column("provider_response_ciphertext", sa.Text(), nullable=True)' in migration


def test_encrypted_checkout_response_is_bound_to_attempt_and_tamper_evident():
    settings = _settings()
    attempt_id = uuid4()
    ciphertext = encrypt_checkout_response(
        settings,
        attempt_id,
        token="snap-token",
        redirect_url="https://app.sandbox.midtrans.com/snap/v2/vtweb/token",
    )

    result = decrypt_checkout_response(settings, attempt_id, ciphertext)
    assert result.token == "snap-token"
    with pytest.raises(ValueError):
        decrypt_checkout_response(settings, uuid4(), ciphertext)


@pytest.mark.parametrize(
    ("provider_status", "fraud_status", "expected"),
    [
        ("pending", "accept", "pending"),
        ("capture", "accept", "paid"),
        ("settlement", "accept", "paid"),
        ("capture", "challenge", None),
        ("deny", "deny", "failed"),
        ("cancel", "accept", "cancelled"),
        ("expire", "accept", "expired"),
        ("refund", "accept", "refunded"),
        ("partial_refund", "accept", None),
        ("chargeback", "accept", None),
        ("partial_chargeback", "accept", None),
        ("authorize", "accept", None),
    ],
)
def test_midtrans_status_mapping(provider_status, fraud_status, expected):
    assert _map_transaction_status(provider_status, fraud_status) == expected
