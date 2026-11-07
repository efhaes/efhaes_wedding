# Entitlements

Personal purchases are invitation-scoped. A trusted backend may provision an
active, unassigned purchase for a user after its payment has been independently
confirmed. Creating a premium personal invitation atomically claims one such
purchase for that user and invitation. The claim is not available to another
user or a second invitation. Pending purchases do not grant access.

When a payment record exists, a personal purchase is considered only when it
has a `paid` record with `paid_at` set. Pending and failed payments cannot grant
access. An active purchase without a payment record is treated as a trusted
non-payment/admin grant and must only be provisioned by backend processes.

The billing API creates pending transactions and exposes read-only status. It
has no public payment-status mutation or activation endpoint. Only the internal
`PaymentLifecycleService` accepts a result after a trusted provider-verification
step; the service updates payment and purchase/subscription in one transaction.
No gateway, webhook verification, checkout URL, or provider callback is
implemented. Client-reported payment status must never be used to activate a
transaction.

Workspace feature access requires an active membership and an active
subscription whose plan includes the feature. Template premium access uses
`premium_templates`. Editor sections and invitation music do not have a
configured entitlement policy in the current catalog and therefore are not
classified as premium by this application.

## Catalog and quotas

The `c51a7d8e2f40` migration adds explicit plan duration configuration and
purchase snapshots. Run the initial catalog seed with
`python -m app.modules.entitlements.seed`. The seed is idempotent and only
inserts missing stable codes; it does not overwrite administrator-managed
values. Free, Premium Personal, and Workspace are inserted inactive with
prices unset; one-time plan durations are pending until product decisions are
supplied. Workspace recurring periods are configured as positive `billing_period_days`
values; no default interval is inferred. A subscription snapshots this period
and starts its single configured period only after verified payment. Automatic
renewal and billing-cycle policy are not implemented.
The `premium_templates` feature is recorded, but no plan-feature mapping is
seeded, so no plan is granted an unapproved feature policy.

Fixed one-time plans require a positive `duration_days`; lifetime one-time plans
must have no duration days. A personal purchase snapshots its duration, price,
currency, and enabled active feature mappings when created. A workspace
subscription snapshots its configured billing period, price, currency, and
enabled active feature mappings. Later mapping or plan changes do not alter
those snapshots. Rows predating snapshots (`entitlement_snapshot IS NULL`)
continue to resolve through their existing plan mappings.

The user API is limited to creating and reading purchases, subscriptions, and
payments under `/api/v1/purchases`, `/api/v1/subscriptions`, and
`/api/v1/payments`. Purchase/subscription creation also creates a pending
payment from the server-side transaction snapshot. Payment details omit
provider references and internal snapshots. Users cannot set amount, currency,
payment status, activation timestamp, or expiry through these endpoints.

`PlanFeature.configuration` and `EntitlementOverride.limit_override` are
unstructured JSON. Although the resolver can read a non-negative numeric
`limit`, there is no declared unit, counting scope, or shared atomic usage
counter. Generic quota enforcement is consequently not implemented.

## Midtrans payment foundation (not enabled)

The application accepts optional `MIDTRANS_SERVER_KEY`,
`MIDTRANS_CLIENT_KEY`, and `MIDTRANS_IS_PRODUCTION` settings. The server key is
a `SecretStr`; it is checked only when an integration explicitly requests it,
so missing gateway credentials do not prevent the application from starting.
`MIDTRANS_IS_PRODUCTION` defaults to `false` (Sandbox). The API base URL is
selected internally as `https://api.sandbox.midtrans.com` or
`https://api.midtrans.com`; callers cannot supply a URL. The client key is
only needed if a future frontend flow requires it and must not be used as a
server credential. `.env.example` contains empty key placeholders only.

Midtrans notification authenticity uses the notification's `signature_key`,
computed as `SHA512(order_id + status_code + gross_amount + ServerKey)`. The
Server Key is the secret input; there is no separate `MIDTRANS_SIGNATURE_KEY`
setting. See [Midtrans notification authenticity
documentation](https://docs.midtrans.com/docs/https-notification-webhooks#b-verifying-notification-authenticity-b).
Signature verification and all notification handling are still unimplemented.

Payments now have nullable `provider_order_id` and
`provider_transaction_id` fields. Uniqueness is scoped to `(provider, ID)`;
legacy rows with `provider IS NULL` remain valid, and
`provider_reference` remains for Batch 18 compatibility. The migration also
makes webhook event uniqueness provider-scoped and adds processing status and
attempt metadata. `processing_attempts` is an integer with a zero default.
Webhook event rows may keep `provider IS NULL` for legacy compatibility, but
new ORM inserts require a non-empty, normalized provider and event ID. The
database check constraint permits NULL only for those legacy rows.

The pure `build_provider_event_id()` helper is implemented; it produces a
SHA-256 digest over canonical JSON with sorted keys and identity version `1`.
The digest uses only provider, `order_id`, `transaction_id`,
`transaction_status`, `status_code`, `gross_amount`, and `currency`.
Provider/status/currency are trimmed and lowercased (currency is canonicalized
to uppercase); order and transaction IDs are trimmed without changing case;
status codes are normalized as decimal digits; and amounts use normalized
Decimal notation. Timestamps (`transaction_time`, `settlement_time`,
`expiry_time`, and `cancel_time`) and `signature_key` are deliberately excluded
from the digest. Thus identical retries with the same status and identity
fields deduplicate despite key ordering or timestamp changes, while a status
change yields a different identity. Provider remains part of the digest and
the database uniqueness key is `(provider, provider_event_id)`.

Two genuinely distinct events whose complete identity fields are identical
cannot be distinguished without an upstream event ID; Midtrans notifications
do not provide one in the documented format. This helper is not connected to a
webhook endpoint and does not yet provide active deduplication. Its tests use
locally constructed notification fixtures, not production or official sample
payloads.

Delayed and out-of-order notifications still require authoritative status
reconciliation; lifecycle transitions currently reject terminal-to-paid
changes. The stable provider order ID should be derived once from the immutable
payment ID and reused for all retries. Persist only an allowlisted, sanitized
event payload; do not retain card data, credentials, or other unnecessary
sensitive notification fields.

The migration downgrade allows legacy processed rows when `processed_at`
reconstructs their migrated `processed` status. It refuses to downgrade when
provider identifiers, non-default processing state/attempts, or duplicate
event IDs across providers would be lost or violate the prior global event ID
constraint.

`POST /purchases` and `POST /subscriptions` now require an
`Idempotency-Key` header containing 16-255 visible characters. The application
stores only its SHA-256 hash and a canonical request fingerprint. A unique
constraint scopes the key by user and operation. The resource and completed
idempotency result are committed in the same database transaction. A matching
key and fingerprint replays the original purchase/subscription and payment;
reusing the key with a different request returns `409`. A different key for a
personal purchase is a new intentional purchase, even for the same plan.
Subscription creation still rejects another pending subscription for the same
workspace.

Idempotency records have no automatic expiry or cleanup policy. Do not delete
or reuse keys while their resource is retained; any retention or tombstone
policy requires a separate product/data-retention decision.

The checkout foundation adds one `CheckoutAttempt` per payment. Its stable
provider order ID is `pay-` plus the payment UUID in hexadecimal (36 characters)
and is copied to the payment row; provider-scoped uniqueness is enforced by
database constraints. Attempts store a request fingerprint, `creating`,
`ready`, `indeterminate`, `failed`, or `resolved` state, a lease and fencing
generation, reconciliation metadata, and state history. Recovery
claims advance the fencing generation; stale workers cannot transition an
attempt. A timeout is not treated as failure: `failed` and `resolved`
transitions require typed, sealed provider-verification evidence bound to the
attempt, provider order ID, and fencing generation. State changes use a
row-lock plus conditional update and only commit when the lease owner,
generation, and expected prior state still match. Indeterminate is the only
state workers may record without provider evidence; it never marks payment as
paid. Retry asks an injected internal provider verifier for explicit
`FAILED_RETRY_SAFE` evidence before locking the attempt, then rechecks the
attempt, order ID, and fencing generation under lock before the conditional
retry update. Evidence from an earlier generation cannot be reused. Without a
configured verifier, retry fails closed. Ambiguous or unverified outcomes
remain on the reconciliation/manual-review path and cannot trigger retry.

`CheckoutAttempt.resolved` means only that checkout processing has been
reconciled and closed. It does not mean payment succeeded and grants no
entitlement. Payment status and entitlement change only through
`PaymentLifecycleService.record_verified_outcome()` after a verified payment
outcome.

The payment foundation migration retains provider-scoped event uniqueness and
adds a partial unique index for legacy rows where `provider IS NULL`. This
preserves the former global event-ID uniqueness for legacy-compatible rows,
which a composite unique constraint alone would not enforce in PostgreSQL.

The authenticated `POST /api/v1/payments/{payment_id}/checkout` endpoint uses
the existing user/workspace authorization, CSRF check, payment snapshot, and
application idempotency record. It sends one Snap create request per newly
claimed attempt, using the stable attempt order ID. The HTTP call happens after
the claim transaction commits; no database lock is held during provider I/O.
An in-flight duplicate receives the existing attempt state. A `ready` replay
returns the same checkout response; `indeterminate` triggers Get Status
reconciliation and never creates a new order automatically.

Snap Server Key and the token-encryption key are required only when checkout or
webhook processing is used. `MIDTRANS_IS_PRODUCTION` selects the internal
Sandbox/Production Snap and Status API hosts. The current integration supports
IDR only and rejects non-integer gross amounts before the Snap request. The
returned redirect URL is accepted only for the exact HTTPS Midtrans host for
the selected environment. Snap token and redirect URL are stored together as
AES-GCM ciphertext bound to the checkout attempt ID; the token is returned only
to the authenticated payment owner or authorized workspace billing member.
The Client Key is not returned because this flow uses the provider redirect URL
rather than loading `snap.js`.

The create request uses a bounded HTTPX timeout. Timeout, network failures,
provider errors, malformed replies, and persistence failures never imply
payment success and never permit another create request. They leave the attempt
indeterminate. Recovery queries Get Transaction Status by transaction ID when
available, otherwise by the stable order ID. A not-found response is not proof
that Midtrans never created the transaction; it only schedules another status
check/manual review. A found pending transaction without a stored Snap token is
also not re-created because the documented Status API does not recover the
Snap token.

`POST /api/v1/payments/midtrans/webhook` is the unauthenticated provider
callback. It verifies the official SHA-512 signature with constant-time
comparison, checks provider order/transaction identity, amount, and currency,
then verifies current transaction status with the Status API before applying a
payment outcome. Only sanitized notification fields are persisted; the
signature and secret are never stored. Event identity is provider-scoped and
status-specific, so retries deduplicate while later status changes remain
separate events. Failed/manual-review events are retained for review; database
or provider availability failures return a retryable error.

Verified `capture`/`settlement` with `fraud_status=accept` maps to paid;
`pending` never activates entitlement. `deny`/`failure`, `cancel`, and `expire`
close only a pending payment. A verified full `refund` revokes the existing
entitlement through `PaymentLifecycleService`. Per product policy, partial
refund, chargeback, partial chargeback, unsafe fraud results, and reversal
(`deny` after a recorded payment) go to manual review and do not automatically
change entitlement. `authorize` is not enabled for this merchant flow and is
not treated as paid. No redirect return or Snap token can settle a payment.

`MidtransCheckoutService.reconcile_due_attempts()` is the bounded recovery
entry point for an external scheduler. It claims a lease, releases database
locks before provider I/O, and uses the existing fencing generation before
applying a verified result. The application does not currently register a
background scheduler, so deployments must wire this callable into their
existing job runner and alert on old indeterminate attempts/manual-review
events before relying on automatic recovery.

Migration `d83f6a1c9b42` follows `b74f19c2e6a1`. New migration
`e4b7c9a13f62` follows `d83f6a1c9b42` and adds only nullable encrypted checkout
response storage; its downgrade refuses to drop non-empty ciphertext. These
migrations have not been executed as part of this implementation.

Subscription creation locks `workspace`, then its billing membership/plan and
subscription rows in stable order. Subscription payment settlement/refund
locks `workspace`, then `payment`, then the target subscription; paid
settlement subsequently locks other active/pending subscriptions ordered by
`created_at` and ID. The workspace lock serializes these workspace-scoped
flows. Personal payment processing locks `payment` then its purchase. This
removes the previously identified workspace-vs-subscription inversion for
related workspace flows. Static review identified a potential deadlock, not a
reproduced one; PostgreSQL concurrency tests were not run in this phase.

PostgreSQL integration tests cover concurrent idempotency claims, concurrent
checkout-attempt claims, stale fencing, duplicate Midtrans webhooks, and
out-of-order notifications. They require `POSTGRES_ENTITLEMENT_TEST_URL`,
verify the exact test database name, and were not run as part of this phase.
HTTPX client, signature, encryption, status mapping, and static migration
unit tests were added but not run.

Migration `b74f19c2e6a1` follows `a6b75c4d2e90`. It has been created but not
executed. Midtrans HTTP calls, Snap checkout, webhook endpoints, automated
settlement, and payment-to-entitlement activation from Midtrans are not
implemented; the gateway cannot accept payments yet.

## Platform administrator catalog API

The platform-admin-only catalog endpoints are under `/api/v1/admin/plans` and
`/api/v1/admin/features`. They authorize through `require_platform_admin`; a
workspace owner or administrator does not receive platform privileges. Every
mutation also requires the existing CSRF cookie/header pair.

Plan codes, billing types, feature codes, and feature types are immutable.
Plans and features are deactivated rather than permanently deleted. Active
plans require an explicitly configured price and duration; active subscription
plans additionally require a positive billing period in days. No price,
duration, or billing period is defaulted by the API. Currency validation checks
a three-letter uppercase code, not membership in a business-approved currency
list.

Personal purchases keep their stored expiry and duration/price snapshots when
plan values change. Mapping changes lock the parent plan row, as do transaction
creation, so each transaction captures a complete mapping state. Catalog
changes are allowed for new transactions while existing snapshots remain
unchanged. Mapping changes remain blocked while a pending or currently valid
legacy transaction has no feature snapshot. Feature deactivation is rejected
while a pending/current transaction snapshot or unexpired override depends on
it.
