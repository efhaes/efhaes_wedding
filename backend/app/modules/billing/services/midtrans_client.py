from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings
from app.modules.billing.services.midtrans_security import parse_midtrans_amount


_HTTP_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class MidtransError(Exception):
    """A provider error safe to handle without exposing response bodies."""


class MidtransTimeout(MidtransError):
    pass


class MidtransNetworkError(MidtransError):
    pass


class MidtransNotFound(MidtransError):
    pass


class MidtransHTTPError(MidtransError):
    def __init__(self, response_status: int) -> None:
        self.response_status = response_status
        super().__init__(f"Midtrans returned HTTP {response_status}")


class MidtransMalformedResponse(MidtransError):
    pass


@dataclass(frozen=True, slots=True)
class SnapCreateResponse:
    token: str = field(repr=False)
    redirect_url: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MidtransTransactionStatus:
    order_id: str
    transaction_id: str
    transaction_status: str
    status_code: str
    gross_amount: Decimal
    currency: str
    fraud_status: str | None
    payment_type: str | None
    transaction_time: str | None


class MidtransSnapClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.server_key = settings.require_midtrans_server_key().get_secret_value()
        self._client = client

    def create_transaction(
        self, *, order_id: str, gross_amount: Decimal
    ) -> SnapCreateResponse:
        self._validate_amount_and_order(order_id, gross_amount)
        if self.settings.require_midtrans_currency() != "IDR":
            raise ValueError("Midtrans Snap integration currently requires IDR")
        response = self._request(
            "POST",
            f"{self.settings.midtrans_snap_base_url}/snap/v1/transactions",
            json={
                "transaction_details": {
                    "order_id": order_id,
                    "gross_amount": int(gross_amount),
                }
            },
        )
        body = self._json_object(response)
        token = body.get("token")
        redirect_url = body.get("redirect_url")
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise MidtransMalformedResponse("Midtrans Snap token is missing")
        if not isinstance(redirect_url, str) or not self._valid_redirect_url(
            redirect_url
        ):
            raise MidtransMalformedResponse("Midtrans redirect URL is invalid")
        return SnapCreateResponse(token=token, redirect_url=redirect_url)

    def get_transaction_status(
        self,
        *,
        order_id: str,
        transaction_id: str | None = None,
    ) -> MidtransTransactionStatus:
        if not order_id or len(order_id) > 50:
            raise ValueError("Midtrans order ID is invalid")
        lookup_id = transaction_id or order_id
        if not lookup_id or len(lookup_id) > 255:
            raise ValueError("Midtrans transaction identity is invalid")
        response = self._request(
            "GET",
            f"{self.settings.midtrans_api_base_url}/v2/"
            f"{quote(lookup_id, safe='')}/status",
        )
        body = self._json_object(response)
        try:
            status_code = _required_string(body, "status_code", maximum=12)
            provider_order_id = _required_string(body, "order_id", maximum=50)
            provider_transaction_id = _required_string(
                body, "transaction_id", maximum=255
            )
            transaction_status = _required_string(
                body, "transaction_status", maximum=80
            ).lower()
            gross_amount = parse_midtrans_amount(body.get("gross_amount"))
            currency = _required_string(body, "currency", maximum=3).upper()
            fraud_status = body.get("fraud_status")
            payment_type = body.get("payment_type")
            transaction_time = body.get("transaction_time")
        except ValueError as error:
            raise MidtransMalformedResponse(
                "Midtrans status response is incomplete"
            ) from error
        if not status_code.isascii() or not status_code.isdigit():
            raise MidtransMalformedResponse(
                "Midtrans status response has an invalid status code"
            )
        return MidtransTransactionStatus(
            order_id=provider_order_id,
            transaction_id=provider_transaction_id,
            transaction_status=transaction_status,
            status_code=status_code,
            gross_amount=gross_amount,
            currency=currency,
            fraud_status=fraud_status if isinstance(fraud_status, str) else None,
            payment_type=payment_type if isinstance(payment_type, str) else None,
            transaction_time=(
                transaction_time if isinstance(transaction_time, str) else None
            ),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = self._client
        if client is None:
            with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as owned:
                return self._send(owned, method, url, kwargs)
        return self._send(client, method, url, kwargs)

    def _send(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        kwargs: dict[str, Any],
    ) -> httpx.Response:
        try:
            response = client.request(
                method,
                url,
                auth=httpx.BasicAuth(self.server_key, ""),
                timeout=_HTTP_TIMEOUT,
                follow_redirects=False,
                headers={"Accept": "application/json"},
                **kwargs,
            )
        except httpx.TimeoutException:
            raise MidtransTimeout("Midtrans request timed out") from None
        except httpx.TransportError:
            raise MidtransNetworkError("Midtrans network request failed") from None
        if response.status_code == 404:
            raise MidtransNotFound("Midtrans transaction was not found")
        if not 200 <= response.status_code < 300:
            raise MidtransHTTPError(response.status_code)
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except (ValueError, UnicodeError):
            raise MidtransMalformedResponse(
                "Midtrans returned malformed JSON"
            ) from None
        if not isinstance(value, dict):
            raise MidtransMalformedResponse(
                "Midtrans returned an invalid response shape"
            )
        return value

    def _valid_redirect_url(self, value: str) -> bool:
        if len(value) > 2048:
            return False
        try:
            parts = urlsplit(value)
            port = parts.port
            expected_host = urlsplit(self.settings.midtrans_snap_base_url).hostname
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and parts.hostname == expected_host
            and port in (None, 443)
            and parts.username is None
            and parts.password is None
            and parts.path.startswith("/snap/v2/vtweb/")
            and not parts.fragment
        )

    @staticmethod
    def _validate_amount_and_order(order_id: str, amount: Decimal) -> None:
        if (
            not isinstance(order_id, str)
            or len(order_id) > 50
            or not order_id
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~-"
                for character in order_id
            )
        ):
            raise ValueError("Midtrans order ID is invalid")
        if (
            not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount <= 0
            or amount != amount.to_integral_value()
        ):
            raise ValueError("Midtrans gross amount must be a positive integer")


def _required_string(
    value: dict[str, Any], key: str, *, maximum: int
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip() or len(item) > maximum:
        raise ValueError(f"Midtrans field {key} is invalid")
    return item.strip()
