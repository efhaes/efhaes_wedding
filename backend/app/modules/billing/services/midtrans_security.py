import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from os import urandom
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings


_SIGNATURE_PATTERN = re.compile(r"[0-9a-fA-F]{128}\Z")
_MAX_AMOUNT = Decimal("9999999999.99")


@dataclass(frozen=True, slots=True)
class MidtransNotification:
    order_id: str
    transaction_id: str
    transaction_status: str
    status_code: str
    gross_amount: Decimal
    currency: str
    signature_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class EncryptedCheckoutResponse:
    token: str
    redirect_url: str


def verify_notification_signature(
    payload: Mapping[str, object], server_key: str
) -> MidtransNotification:
    order_id = _required_raw_string(payload, "order_id", maximum=50)
    transaction_id = _required_string(payload, "transaction_id", maximum=255)
    transaction_status = _required_string(payload, "transaction_status", maximum=80)
    status_code = _required_raw_string(payload, "status_code", maximum=12)
    gross_amount_text = _required_raw_string(payload, "gross_amount", maximum=64)
    currency = _required_string(payload, "currency", maximum=3)
    signature_key = _required_raw_string(payload, "signature_key", maximum=128)
    if not _SIGNATURE_PATTERN.fullmatch(signature_key):
        raise ValueError("Midtrans notification signature is malformed")
    gross_amount = _parse_amount(gross_amount_text)
    expected_signature = hashlib.sha512(
        (
            order_id
            + status_code
            + gross_amount_text
            + server_key
        ).encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature_key.lower()):
        raise ValueError("Midtrans notification signature is invalid")

    return MidtransNotification(
        order_id=order_id,
        transaction_id=transaction_id,
        transaction_status=transaction_status.lower(),
        status_code=status_code,
        gross_amount=gross_amount,
        currency=currency.upper(),
        signature_key=signature_key,
    )


def encrypt_checkout_response(
    settings: Settings,
    attempt_id: UUID,
    *,
    token: str,
    redirect_url: str,
) -> str:
    if not token or len(token) > 4096 or not redirect_url or len(redirect_url) > 2048:
        raise ValueError("Midtrans checkout response is invalid")
    secret = settings.require_midtrans_token_encryption_key().get_secret_value()
    key = hashlib.sha256(
        b"efhaeswedding:midtrans:snap-response:v1:" + secret.encode("utf-8")
    ).digest()
    nonce = urandom(12)
    plaintext = json.dumps(
        {"redirect_url": redirect_url, "token": token},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, attempt_id.bytes)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_checkout_response(
    settings: Settings, attempt_id: UUID, ciphertext: str
) -> EncryptedCheckoutResponse:
    secret = settings.require_midtrans_token_encryption_key().get_secret_value()
    key = hashlib.sha256(
        b"efhaeswedding:midtrans:snap-response:v1:" + secret.encode("utf-8")
    ).digest()
    try:
        encrypted = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        nonce, body = encrypted[:12], encrypted[12:]
        plaintext = AESGCM(key).decrypt(nonce, body, attempt_id.bytes)
        value = json.loads(plaintext)
    except (
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
        InvalidTag,
    ) as error:
        raise ValueError("Stored Midtrans checkout response cannot be decrypted") from error

    if (
        not isinstance(value, dict)
        or not isinstance(value.get("token"), str)
        or not isinstance(value.get("redirect_url"), str)
    ):
        raise ValueError("Stored Midtrans checkout response is malformed")
    return EncryptedCheckoutResponse(
        token=value["token"],
        redirect_url=value["redirect_url"],
    )


def parse_midtrans_amount(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Midtrans amount is invalid")
    return _parse_amount(str(value))


def _required_string(
    payload: Mapping[str, object], field: str, *, maximum: int
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Midtrans notification field {field} is invalid")
    return value.strip()


def _required_raw_string(
    payload: Mapping[str, object], field: str, *, maximum: int
) -> str:
    value = payload.get(field)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"Midtrans notification field {field} is invalid")
    return value


def _parse_amount(value: str) -> Decimal:
    if not value or len(value) > 64:
        raise ValueError("Midtrans amount is invalid")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Midtrans amount is invalid") from error
    if not amount.is_finite() or amount < 0 or amount > _MAX_AMOUNT:
        raise ValueError("Midtrans amount is invalid")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Midtrans amount precision is invalid")
    return amount
