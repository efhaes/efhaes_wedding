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
