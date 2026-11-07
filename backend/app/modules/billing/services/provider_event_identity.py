import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


_PROVIDER_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_REQUIRED_FIELDS = (
    "order_id",
    "transaction_id",
    "transaction_status",
    "status_code",
    "gross_amount",
    "currency",
)
_IDENTITY_FIELDS = (
    "order_id",
    "transaction_id",
    "transaction_status",
    "status_code",
    "gross_amount",
    "currency",
)


def build_provider_event_id(
    provider: str,
    notification: Mapping[str, object],
) -> str:
    """Build a stable digest from normalized Midtrans transaction identity fields."""
    if not isinstance(provider, str):
        raise ValueError("Payment provider is invalid")
    normalized_provider = provider.strip().lower()
    if not _PROVIDER_PATTERN.fullmatch(normalized_provider):
        raise ValueError("Payment provider is invalid")

    normalized: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        if field not in notification or notification[field] is None:
            raise ValueError(f"Notification field {field} is required")

    for field in _IDENTITY_FIELDS:
        value = notification.get(field)
        if field == "gross_amount":
            normalized[field] = _normalize_amount(value)
        elif field == "currency":
            normalized[field] = _normalize_currency(value)
        elif field == "transaction_status":
            normalized[field] = _normalize_status(value)
        elif field == "status_code":
            normalized[field] = _normalize_status_code(value)
        elif isinstance(value, str):
            normalized_value = str(value).strip()
            if not normalized_value:
                raise ValueError(f"Notification field {field} cannot be empty")
            normalized[field] = normalized_value
        else:
            raise ValueError(f"Notification field {field} has an invalid value")

    canonical = json.dumps(
        {
            "version": 1,
            "provider": normalized_provider,
            "notification": normalized,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_amount(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Notification gross_amount is invalid")
    raw_value = str(value)
    if len(raw_value) > 64:
        raise ValueError("Notification gross_amount is invalid")
    try:
        amount = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError("Notification gross_amount is invalid") from error
    if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
        raise ValueError("Notification gross_amount is invalid")
    if amount == 0:
        return "0"
    digits = list(amount.as_tuple().digits)
    exponent = amount.as_tuple().exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -2:
        raise ValueError("Notification gross_amount has unsupported precision")
    normalized = format(amount, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _normalize_currency(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.strip()) != 3
        or not value.strip().isascii()
        or not value.strip().isalpha()
    ):
        raise ValueError("Notification currency is invalid")
    return value.strip().upper()


def _normalize_status(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Notification transaction_status is invalid")
    return value.strip().lower()


def _normalize_status_code(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Notification status_code is invalid")
    normalized = str(value).strip()
    if not normalized.isascii() or not normalized.isdigit():
        raise ValueError("Notification status_code is invalid")
    return str(int(normalized))
