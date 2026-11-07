# Security Configuration

## Rate limiting

Rate-limit counters are stored in the `rate_limit_buckets` database table and
updated atomically, so limits are shared by application workers using the same
database. Apply Alembic migrations through the normal deployment process before
starting the updated application. The bucket key is an HMAC of the policy and
client address; raw client IP addresses are not stored.

The following environment settings control the per-client-IP request budget:

| Policy | Requests | Window |
| --- | --- | --- |
| Login | `RATE_LIMIT_LOGIN_REQUESTS=10` | `RATE_LIMIT_LOGIN_WINDOW_SECONDS=900` |
| Registration | `RATE_LIMIT_REGISTER_REQUESTS=5` | `RATE_LIMIT_REGISTER_WINDOW_SECONDS=3600` |
| Refresh token | `RATE_LIMIT_REFRESH_REQUESTS=30` | `RATE_LIMIT_REFRESH_WINDOW_SECONDS=60` |
| Public reads | `RATE_LIMIT_PUBLIC_REQUESTS=60` | `RATE_LIMIT_PUBLIC_WINDOW_SECONDS=60` |
| Public submissions | `RATE_LIMIT_SUBMISSION_REQUESTS=10` | `RATE_LIMIT_SUBMISSION_WINDOW_SECONDS=60` |
| Guest operations | `RATE_LIMIT_GUEST_ACCESS_REQUESTS=30` | `RATE_LIMIT_GUEST_ACCESS_WINDOW_SECONDS=60` |
| Media uploads | `RATE_LIMIT_MEDIA_UPLOAD_REQUESTS=10` | `RATE_LIMIT_MEDIA_UPLOAD_WINDOW_SECONDS=60` |

Requests over a limit return HTTP 429 with a `Retry-After` header. Login limits
are per client IP, not per account, to avoid letting an attacker lock out a
victim by targeting their email address.

## Reverse proxy and client addresses

Do not configure Uvicorn to trust forwarded headers from arbitrary sources.
When running behind a reverse proxy, enable proxy headers and list only the
proxy's known source addresses; for example:

```text
uvicorn app.main:app --proxy-headers --forwarded-allow-ips="127.0.0.1"
```

Replace `127.0.0.1` with the actual address(es) from which the trusted proxy
connects. Configure that proxy to overwrite/sanitize `X-Forwarded-For` rather
than forwarding a client-supplied value as authoritative. Never use
`--forwarded-allow-ips="*"` on an internet-facing deployment. If the service is
directly internet-facing, do not enable trust for forwarded headers.

## Request and upload sizes

`PUBLIC_REQUEST_BODY_MAX_BYTES` defaults to 16 KiB and is enforced while
streaming request bodies, including when `Content-Length` is absent.
`MEDIA_MAX_FILE_SIZE_MB` defaults to 5 MiB and
`MUSIC_MAX_FILE_SIZE_MB` defaults to 10 MiB. Multipart endpoints also allow
`REQUEST_BODY_MULTIPART_OVERHEAD_BYTES` (64 KiB by default) for multipart
framing. Configure the reverse proxy's body-size limits at least as
restrictively as the corresponding application limits; apply separate limits
for public submissions, image uploads, and music uploads where supported.

Image uploads accept JPEG, PNG, and WebP only. The decoded image may not exceed
10,000 pixels on either edge or 40 megapixels total. Files are decoded before
storage and use random storage keys.

## Guest links and API compatibility

Personalized guest links now put the token in a URL fragment (`#to=...`), which
browsers do not send in HTTP requests or `Referer` headers. The frontend should
read the fragment and send the token as `Authorization: Bearer <token>` for
personalized invitation, RSVP, and guestbook operations. After reading it, the
frontend should remove the fragment from the visible URL with
`history.replaceState`. Do not move the token into a query parameter.

The API temporarily accepts legacy `guest_token` query parameters for
compatibility. Application middleware removes that parameter before the ASGI
request is logged, but a reverse proxy may log the original URL before it
reaches the application. Remove old query-based links after clients have
migrated. Guest tokens are stored as SHA-256 hashes and regenerating a link
invalidates the previous token.

## Registration response

`POST /api/v1/auth/register` returns HTTP 202 and the same generic response
whether an email address is new or already registered. Clients should display
the generic response and continue through the normal sign-in flow instead of
using registration to determine account existence.

## Midtrans checkout

Midtrans remains disabled until its credentials are configured. Sandbox is the
default; `MIDTRANS_IS_PRODUCTION=true` selects the Production hosts. Configure
`MIDTRANS_SERVER_KEY` only as a server-side secret. The current redirect-based
checkout does not expose or require `MIDTRANS_CLIENT_KEY`.

Configure `MIDTRANS_TOKEN_ENCRYPTION_KEY` as a separate random secret of at
least 32 characters. It is used to encrypt Snap token/redirect response data
before database storage and is not required for application startup. Rotating
this key requires re-encrypting retained checkout responses before removing
the old key; otherwise those responses cannot be replayed. The integration
currently accepts only the configured merchant currency `IDR`.

Never include Midtrans keys, webhook signatures, Snap tokens, or provider
response bodies in logs. The webhook endpoint is intentionally unauthenticated
for provider delivery and validates the Midtrans SHA-512 signature before
processing; do not protect it with browser CSRF or user-cookie authentication.
