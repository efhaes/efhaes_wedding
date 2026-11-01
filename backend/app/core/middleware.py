import json
from urllib.parse import parse_qsl, urlencode

from app.core.config import settings


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.app(scope, receive, send)
            return

        limit = _request_body_limit(scope["path"])
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if content_lengths:
            if len(set(content_lengths)) != 1 or not content_lengths[0].isdigit():
                await _send_error(send, 400, "Invalid Content-Length")
                return
            if int(content_lengths[0]) > limit:
                await _send_error(send, 413, "Request body too large")
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await _send_error(send, 413, "Request body too large")


def _request_body_limit(path: str) -> int | None:
    if path.startswith("/api/v1/public/"):
        return settings.PUBLIC_REQUEST_BODY_MAX_BYTES
    if path.startswith("/api/v1/invitations/") and path.endswith("/media"):
        return (
            settings.MEDIA_MAX_FILE_SIZE_MB * 1024 * 1024
            + settings.REQUEST_BODY_MULTIPART_OVERHEAD_BYTES
        )
    if path.startswith("/api/v1/invitations/") and path.endswith("/music"):
        return (
            settings.MUSIC_MAX_FILE_SIZE_MB * 1024 * 1024
            + settings.REQUEST_BODY_MULTIPART_OVERHEAD_BYTES
        )
    return None


async def _send_error(send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LegacyGuestTokenQueryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("query_string"):
            query = scope["query_string"].decode("latin-1")
            pairs = parse_qsl(query, keep_blank_values=True)
            legacy_tokens = [value for key, value in pairs if key == "guest_token"]
            if legacy_tokens:
                scope.setdefault("state", {})["legacy_guest_token"] = legacy_tokens[-1]
                safe_pairs = [(key, value) for key, value in pairs if key != "guest_token"]
                safe_query = urlencode(safe_pairs).encode("latin-1")
                scope["query_string"] = safe_query
                raw_path = scope.get("path", "").encode("utf-8")
                scope["raw_path"] = raw_path + (b"?" + safe_query if safe_query else b"")
        await self.app(scope, receive, send)
