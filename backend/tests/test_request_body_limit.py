import anyio

from app.core.middleware import RequestBodyLimitMiddleware


def test_public_body_limit_rejects_streamed_body_without_content_length():
    response_messages = []
    request_messages = [
        {
            "type": "http.request",
            "body": b"x" * 10_000,
            "more_body": True,
        },
        {
            "type": "http.request",
            "body": b"x" * 10_000,
            "more_body": False,
        },
    ]

    async def app(_scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def run_middleware():
        middleware = RequestBodyLimitMiddleware(app)

        async def receive():
            return request_messages.pop(0)

        async def send(message):
            response_messages.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/public/invitations/sample/rsvp",
                "raw_path": b"/api/v1/public/invitations/sample/rsvp",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )

    anyio.run(run_middleware)

    assert response_messages[0]["status"] == 413
    assert response_messages[1]["body"] == b'{"detail":"Request body too large"}'
