from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, get_db
from app.database.base import Base
from app.main import app
from app.models.client import Client
from app.models.billing_idempotency_record import BillingIdempotencyRecord
from app.models.checkout_attempt import CheckoutAttempt, CheckoutAttemptHistory
from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.models.invitation_section import InvitationSection
from app.modules.billing.services.billing_service import BillingService
from app.modules.billing.services.checkout_foundation_service import (
    CheckoutAttemptEvidenceState,
    CheckoutFoundationService,
    CheckoutAttemptVerificationContext,
    VerifiedCheckoutAttemptEvidence,
    _issue_provider_evidence,
    build_provider_order_id,
)
from app.modules.billing.services.idempotency import hash_idempotency_key
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
    VerifiedPaymentOutcome,
)
from app.modules.entitlements.services.catalog_admin_service import (
    CatalogAdminService,
)
from app.modules.entitlements.schemas.admin_catalog import PlanFeatureUpdate
from app.modules.entitlements.services.entitlement_resolver import EntitlementResolver
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.schemas.invitation import InvitationCreate
from app.modules.invitations.services.invitation_service import InvitationService
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def billing_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Client.__table__,
        Template.__table__,
        Invitation.__table__,
        InvitationSection.__table__,
        Plan.__table__,
        Feature.__table__,
        PlanFeature.__table__,
        PersonalPurchase.__table__,
        Subscription.__table__,
        Payment.__table__,
        CheckoutAttempt.__table__,
        CheckoutAttemptHistory.__table__,
        BillingIdempotencyRecord.__table__,
        EntitlementOverride.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    owner = User(
        name="Billing owner",
        email=f"owner-{uuid4().hex}@example.invalid",
        password_hash="test-only",
    )
    outsider = User(
        name="Billing outsider",
        email=f"outsider-{uuid4().hex}@example.invalid",
        password_hash="test-only",
        platform_role="admin",
    )
    premium_template = Template(
        name="Premium fixture",
        slug=f"premium-{uuid4().hex}",
        category="test",
        access_level="premium",
        configuration={},
        status="published",
        is_active=True,
    )
    feature = Feature(
        code="premium_templates",
        name="Premium templates",
        feature_type="boolean",
        is_active=True,
    )
    quota_feature = Feature(
        code="guest_quota",
        name="Guest quota",
        feature_type="limit",
        is_active=True,
    )
    personal_plan = Plan(
        code=f"personal-{uuid4().hex}",
        name="Personal plan",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=60,
        price=Decimal("12.50"),
        currency="USD",
        is_active=True,
    )
    workspace_plan = Plan(
        code=f"workspace-{uuid4().hex}",
        name="Workspace plan",
        billing_type="subscription",
        duration_type="recurring",
        billing_period_days=30,
        price=Decimal("49.00"),
        currency="USD",
        is_active=True,
    )
    with test_session() as session:
        session.add_all(
            [
                owner,
                outsider,
                premium_template,
                feature,
                quota_feature,
                personal_plan,
                workspace_plan,
            ]
        )
        session.flush()
        premium_mapping = PlanFeature(
            plan_id=personal_plan.id,
            feature_id=feature.id,
            is_enabled=True,
            configuration={},
        )
        quota_mapping = PlanFeature(
            plan_id=personal_plan.id,
            feature_id=quota_feature.id,
            is_enabled=True,
            configuration={"limit": 3},
        )
        session.add_all(
            [
                premium_mapping,
                quota_mapping,
                PlanFeature(
                    plan_id=workspace_plan.id,
                    feature_id=feature.id,
                    is_enabled=True,
                    configuration={},
                ),
            ]
        )
        session.commit()

    identity: dict[str, User | None] = {"user": owner}

    def override_get_db():
        with test_session() as session:
            yield session

    def override_current_user():
        return identity["user"]

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    with TestClient(app) as client:
        yield {
            "client": client,
            "session_factory": test_session,
            "identity": identity,
            "owner": owner,
            "outsider": outsider,
            "premium_template": premium_template,
            "feature": feature,
            "quota_feature": quota_feature,
            "personal_plan": personal_plan,
            "workspace_plan": workspace_plan,
            "premium_mapping": premium_mapping,
            "quota_mapping": quota_mapping,
        }
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _invitation(session, owner: User, template: Template) -> Invitation:
    invitation = Invitation(
        owner_id=owner.id,
        template_id=template.id,
        title="Snapshot fixture",
        slug=f"snapshot-{uuid4().hex}",
        status="draft",
    )
    session.add(invitation)
    session.commit()
    return invitation


def _workspace(session, owner: User) -> Workspace:
    workspace = Workspace(
        name="Subscription lifecycle workspace",
        slug=f"subscription-lifecycle-{uuid4().hex}",
        owner_id=owner.id,
        status="active",
    )
    session.add_all(
        [
            workspace,
            WorkspaceMember(
                workspace=workspace,
                user_id=owner.id,
                role="owner",
                status="active",
            ),
        ]
    )
    session.commit()
    return workspace


def _create_personal_purchase(session, user, plan_id):
    return BillingService(session).create_personal_purchase(
        user, plan_id, uuid4().hex
    )


def _create_workspace_subscription(session, user, workspace_id, plan_id):
    return BillingService(session).create_workspace_subscription(
        user, workspace_id, plan_id, uuid4().hex
    )


def _verified_checkout_evidence_for_test(
    attempt, state, reason_code, fencing_generation
):
    return _issue_provider_evidence(
        CheckoutAttemptVerificationContext(
            attempt_id=attempt.id,
            provider_order_id=attempt.provider_order_id,
            fencing_generation=fencing_generation,
        ),
        state,
        reason_code,
    )


class _TestRetrySafetyVerifier:
    def __init__(self, state: CheckoutAttemptEvidenceState | None) -> None:
        self.state = state

    def verify_no_active_transaction(
        self, context: CheckoutAttemptVerificationContext
    ) -> VerifiedCheckoutAttemptEvidence | None:
        if self.state is None:
            return None
        return _issue_provider_evidence(
            context,
            self.state,
            "provider_absence_retry_safe",
        )


class _StaleGenerationRetryVerifier:
    def verify_no_active_transaction(
        self, context: CheckoutAttemptVerificationContext
    ) -> VerifiedCheckoutAttemptEvidence:
        stale_context = CheckoutAttemptVerificationContext(
            attempt_id=context.attempt_id,
            provider_order_id=context.provider_order_id,
            fencing_generation=context.fencing_generation + 1,
        )
        return _issue_provider_evidence(
            stale_context,
            CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE,
            "stale_provider_absence",
        )


def test_personal_purchase_idempotency_replays_and_new_keys_create_new_purchases(
    billing_context,
):
    context = billing_context
    key = "purchase-idempotency-key-001"
    with context["session_factory"]() as session:
        service = BillingService(session)
        first = service.create_personal_purchase(
            context["owner"], context["personal_plan"].id, key
        )
        replay = service.create_personal_purchase(
            context["owner"], context["personal_plan"].id, key
        )
        intentional_new_purchase = service.create_personal_purchase(
            context["owner"],
            context["personal_plan"].id,
            "purchase-idempotency-key-002",
        )

        assert replay.id == first.id
        assert replay.payment.id == first.payment.id
        assert intentional_new_purchase.id != first.id
        purchase_count = session.scalar(
            select(func.count()).select_from(PersonalPurchase).where(
                PersonalPurchase.user_id == context["owner"].id
            )
        )
        assert purchase_count == 2
        record = session.scalar(
            select(BillingIdempotencyRecord).where(
                BillingIdempotencyRecord.user_id == context["owner"].id,
                BillingIdempotencyRecord.operation == "personal_purchase",
                BillingIdempotencyRecord.key_hash == hash_idempotency_key(key),
            )
        )
        assert record.key_hash != key
        assert record.replay_count == 1


def test_personal_purchase_idempotency_key_payload_conflict_is_409(billing_context):
    context = billing_context
    key = "purchase-idempotency-key-conflict"
    with context["session_factory"]() as session:
        service = BillingService(session)
        service.create_personal_purchase(
            context["owner"], context["personal_plan"].id, key
        )

        with pytest.raises(HTTPException) as conflict:
            service.create_personal_purchase(
                context["owner"], context["workspace_plan"].id, key
            )

        assert conflict.value.status_code == 409


def test_workspace_subscription_idempotency_replays_original_resource(billing_context):
    context = billing_context
    key = "workspace-subscription-idempotency-key"
    with context["session_factory"]() as session:
        workspace = _workspace(session, context["owner"])
        service = BillingService(session)
        first = service.create_workspace_subscription(
            context["owner"], workspace.id, context["workspace_plan"].id, key
        )
        replay = service.create_workspace_subscription(
            context["owner"], workspace.id, context["workspace_plan"].id, key
        )

        assert replay.id == first.id
        assert replay.payment.id == first.payment.id
        with pytest.raises(HTTPException) as second_pending:
            service.create_workspace_subscription(
                context["owner"],
                workspace.id,
                context["workspace_plan"].id,
                "workspace-subscription-idempotency-key-2",
            )
        assert second_pending.value.status_code == 409


def test_checkout_claim_is_idempotent_and_order_id_is_stable(billing_context):
    context = billing_context
    with context["session_factory"]() as session:
        purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        first = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-idempotency-key-001",
            worker_id="api-worker-1",
        )
        replay = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-idempotency-key-001",
            worker_id="api-worker-2",
        )
        different_key = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-idempotency-key-002",
            worker_id="api-worker-3",
        )
        payment = session.get(Payment, purchase.payment.id)

        assert replay.id == first.id == different_key.id
        assert first.provider_order_id == build_provider_order_id(payment.id)
        assert payment.provider_order_id == first.provider_order_id
        assert first.status == "creating"
        assert session.scalar(
            select(CheckoutAttempt.id).where(
                CheckoutAttempt.payment_id == payment.id
            )
        ) == first.id
        assert not hasattr(first, "snap_token")


def test_checkout_idempotency_key_cannot_be_reused_for_another_payment(
    billing_context,
):
    context = billing_context
    with context["session_factory"]() as session:
        first_purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        second_purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        key = "checkout-idempotency-key-conflict"
        service.claim_attempt(
            context["owner"],
            first_purchase.payment.id,
            key,
            worker_id="api-worker-1",
        )

        with pytest.raises(HTTPException) as conflict:
            service.claim_attempt(
                context["owner"],
                second_purchase.payment.id,
                key,
                worker_id="api-worker-2",
            )

        assert conflict.value.status_code == 409


def test_checkout_lease_expiry_and_fencing_reject_stale_worker(billing_context):
    context = billing_context
    start = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    with context["session_factory"]() as session:
        purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        attempt = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-lease-idempotency-key",
            worker_id="worker-old",
            lease_duration=timedelta(seconds=5),
            now=start,
        )

        recovered = service.claim_reconciliation_lease(
            attempt.id,
            worker_id="worker-new",
            lease_duration=timedelta(seconds=5),
            now=start + timedelta(seconds=6),
        )
        assert recovered.status == "indeterminate"
        assert recovered.fencing_generation == 2
        assert recovered.lease_owner == "worker-new"

        with pytest.raises(HTTPException) as stale_worker:
            service.mark_indeterminate(
                attempt.id,
                worker_id="worker-old",
                fencing_generation=1,
                reason_code="late_response",
                now=start + timedelta(seconds=7),
            )
        assert stale_worker.value.status_code == 409
        assert session.get(Payment, purchase.payment.id).status == "pending"


def test_checkout_terminal_transitions_require_typed_provider_evidence(
    billing_context
):
    context = billing_context
    now = datetime.now(timezone.utc)
    with context["session_factory"]() as session:
        purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        attempt = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-state-idempotency-key",
            worker_id="worker-1",
            now=now,
        )

        with pytest.raises(TypeError):
            VerifiedCheckoutAttemptEvidence(
                attempt.id,
                attempt.provider_order_id,
                1,
                CheckoutAttemptEvidenceState.READY,
                "provider_response",
            )

        with pytest.raises(HTTPException) as unverified_ready:
            service.apply_verified_provider_evidence(
                attempt.id,
                worker_id="worker-1",
                fencing_generation=1,
                evidence="ready",
                now=now + timedelta(seconds=1),
            )
        assert unverified_ready.value.status_code == 409

        ready = service.apply_verified_provider_evidence(
            attempt.id,
            worker_id="worker-1",
            fencing_generation=1,
            evidence=_verified_checkout_evidence_for_test(
                attempt,
                CheckoutAttemptEvidenceState.READY,
                "provider_response",
                1,
            ),
            now=now + timedelta(seconds=1),
        )
        assert ready.status == "ready"

        reconciliation = service.claim_reconciliation_lease(
            attempt.id,
            worker_id="worker-2",
            now=now + timedelta(seconds=2),
        )
        assert reconciliation.fencing_generation == 2

        resolved = service.apply_verified_provider_evidence(
            attempt.id,
            worker_id="worker-2",
            fencing_generation=2,
            evidence=_verified_checkout_evidence_for_test(
                attempt,
                CheckoutAttemptEvidenceState.RESOLVED,
                "provider_terminal",
                2,
            ),
            now=now + timedelta(seconds=3),
        )
        assert resolved.status == "resolved"
        assert session.get(Payment, purchase.payment.id).status == "pending"
        resolved_purchase = session.get(PersonalPurchase, purchase.id)
        assert resolved_purchase.status == "pending"
        assert resolved_purchase.activated_at is None

        invitation = _invitation(
            session, context["owner"], context["premium_template"]
        )
        resolved_purchase.invitation_id = invitation.id
        session.commit()
        personal_access = EntitlementResolver(session).evaluate_feature_access(
            "premium_templates",
            user_id=context["owner"].id,
            invitation_id=invitation.id,
        )
        assert not personal_access.allowed

        with pytest.raises(HTTPException) as unsafe_failure:
            service.mark_indeterminate(
                attempt.id,
                worker_id="worker-1",
                fencing_generation=1,
                reason_code="not_found",
                now=now + timedelta(seconds=4),
            )
        assert unsafe_failure.value.status_code == 409
        assert session.get(Payment, purchase.payment.id).status == "pending"

        workspace = _workspace(session, context["owner"])
        subscription = _create_workspace_subscription(
            session,
            context["owner"],
            workspace.id,
            context["workspace_plan"].id,
        )
        subscription_attempt = service.claim_attempt(
            context["owner"],
            subscription.payment.id,
            "checkout-resolved-subscription-key",
            worker_id="subscription-worker-1",
            now=now + timedelta(seconds=10),
        )
        service.apply_verified_provider_evidence(
            subscription_attempt.id,
            worker_id="subscription-worker-1",
            fencing_generation=1,
            evidence=_verified_checkout_evidence_for_test(
                subscription_attempt,
                CheckoutAttemptEvidenceState.READY,
                "provider_response",
                1,
            ),
            now=now + timedelta(seconds=11),
        )
        subscription_reconciliation = service.claim_reconciliation_lease(
            subscription_attempt.id,
            worker_id="subscription-worker-2",
            now=now + timedelta(seconds=12),
        )
        subscription_resolved = service.apply_verified_provider_evidence(
            subscription_attempt.id,
            worker_id="subscription-worker-2",
            fencing_generation=subscription_reconciliation.fencing_generation,
            evidence=_verified_checkout_evidence_for_test(
                subscription_attempt,
                CheckoutAttemptEvidenceState.RESOLVED,
                "provider_terminal",
                subscription_reconciliation.fencing_generation,
            ),
            now=now + timedelta(seconds=13),
        )
        assert subscription_resolved.status == "resolved"
        assert session.get(Subscription, subscription.id).status == "pending"
        assert session.get(Payment, subscription.payment.id).status == "pending"
        workspace_access = EntitlementResolver(session).evaluate_feature_access(
            "premium_templates",
            user_id=context["owner"].id,
            workspace_id=workspace.id,
        )
        assert not workspace_access.allowed


def test_checkout_failure_retry_requires_attempt_bound_evidence(
    billing_context
):
    context = billing_context
    now = datetime.now(timezone.utc)
    with context["session_factory"]() as session:
        purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        attempt = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-failure-evidence-key",
            worker_id="worker-1",
            now=now,
        )
        absence_evidence = _verified_checkout_evidence_for_test(
            attempt,
            CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE,
            "provider_absence_retry_safe",
            1,
        )
        failed = service.apply_verified_provider_evidence(
            attempt.id,
            worker_id="worker-1",
            fencing_generation=1,
            evidence=absence_evidence,
            now=now + timedelta(seconds=1),
        )
        assert failed.status == "failed"

        with pytest.raises(HTTPException) as unverified_retry:
            service.retry_failed_attempt(
                attempt.id,
                worker_id="worker-2",
                fencing_generation=1,
                now=now + timedelta(seconds=2),
            )
        assert unverified_retry.value.status_code == 409

        unsafe_verifier_service = CheckoutFoundationService(
            session,
            retry_safety_verifier=_TestRetrySafetyVerifier(
                CheckoutAttemptEvidenceState.READY
            ),
        )
        with pytest.raises(HTTPException) as unsafe_verifier:
            unsafe_verifier_service.retry_failed_attempt(
                attempt.id,
                worker_id="worker-2",
                fencing_generation=1,
                now=now + timedelta(seconds=2),
            )
        assert unsafe_verifier.value.status_code == 409
        assert session.get(CheckoutAttempt, attempt.id).status == "failed"

        stale_verifier_service = CheckoutFoundationService(
            session,
            retry_safety_verifier=_StaleGenerationRetryVerifier(),
        )
        with pytest.raises(HTTPException) as stale_evidence:
            stale_verifier_service.retry_failed_attempt(
                attempt.id,
                worker_id="worker-2",
                fencing_generation=1,
                now=now + timedelta(seconds=2),
            )
        assert stale_evidence.value.status_code == 409
        assert session.get(CheckoutAttempt, attempt.id).status == "failed"

        safe_verifier_service = CheckoutFoundationService(
            session,
            retry_safety_verifier=_TestRetrySafetyVerifier(
                CheckoutAttemptEvidenceState.FAILED_RETRY_SAFE
            ),
        )
        retried = safe_verifier_service.retry_failed_attempt(
            attempt.id,
            worker_id="worker-2",
            fencing_generation=1,
            now=now + timedelta(seconds=2),
        )
        assert retried.status == "creating"
        assert retried.fencing_generation == 2
        assert retried.lease_owner == "worker-2"
        assert retried.provider_order_id == attempt.provider_order_id

        with pytest.raises(HTTPException) as stale_result:
            service.apply_verified_provider_evidence(
                attempt.id,
                worker_id="worker-2",
                fencing_generation=2,
                evidence=absence_evidence,
                now=now + timedelta(seconds=3),
            )
        assert stale_result.value.status_code == 409


def test_ambiguous_checkout_attempt_stays_indeterminate_without_safe_retry_evidence(
    billing_context
):
    context = billing_context
    now = datetime.now(timezone.utc)
    with context["session_factory"]() as session:
        purchase = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        service = CheckoutFoundationService(session)
        attempt = service.claim_attempt(
            context["owner"],
            purchase.payment.id,
            "checkout-ambiguous-retry-key",
            worker_id="worker-1",
            now=now,
        )
        indeterminate = service.mark_indeterminate(
            attempt.id,
            worker_id="worker-1",
            fencing_generation=1,
            reason_code="provider_result_ambiguous",
            now=now + timedelta(seconds=1),
        )
        with pytest.raises(HTTPException) as unsafe_retry:
            unsafe_retry_service = CheckoutFoundationService(
                session,
                retry_safety_verifier=_TestRetrySafetyVerifier(
                    CheckoutAttemptEvidenceState.READY
                ),
            )
            unsafe_retry_service.retry_failed_attempt(
                attempt.id,
                worker_id="worker-2",
                fencing_generation=1,
                now=now + timedelta(seconds=2),
            )
        assert unsafe_retry.value.status_code == 409
        assert session.get(CheckoutAttempt, attempt.id).status == "indeterminate"
        assert indeterminate.provider_order_id == attempt.provider_order_id


def test_workspace_checkout_requires_active_billing_role(billing_context):
    context = billing_context
    with context["session_factory"]() as session:
        workspace = _workspace(session, context["owner"])
        subscription = _create_workspace_subscription(
            session,
            context["owner"],
            workspace.id,
            context["workspace_plan"].id,
        )
        membership = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == context["owner"].id,
            )
        )
        membership.role = "member"
        session.commit()

        with pytest.raises(HTTPException) as unauthorized:
            CheckoutFoundationService(session).claim_attempt(
                context["owner"],
                subscription.payment.id,
                "workspace-checkout-idempotency-key",
                worker_id="api-worker",
            )

        assert unauthorized.value.status_code == 404
        assert session.scalar(
            select(CheckoutAttempt.id).where(
                CheckoutAttempt.payment_id == subscription.payment.id
            )
        ) is None


def test_personal_purchase_snapshots_features_and_settlement_is_idempotent(
    billing_context,
):
    context = billing_context
    owner = context["owner"]
    plan = context["personal_plan"]
    mapping = context["quota_mapping"]
    session_factory = context["session_factory"]

    with session_factory() as session:
        purchase_read = _create_personal_purchase(session, owner, plan.id)
        purchase = session.get(PersonalPurchase, purchase_read.id)
        payment = session.get(Payment, purchase_read.payment.id)
        invitation = _invitation(
            session, owner, context["premium_template"]
        )
        purchase.invitation_id = invitation.id
        session.commit()
        assert purchase.status == "pending"
        assert payment.status == "pending"
        assert payment.amount == Decimal("12.50")
        assert purchase.entitlement_snapshot["features"]["guest_quota"][
            "configuration"
        ] == {"limit": 3}

        pending = EntitlementResolver(session).evaluate_feature_access(
            "guest_quota", user_id=owner.id, invitation_id=invitation.id
        )
        assert pending.allowed is False

        CatalogAdminService(session).update_plan_feature(
            plan.id,
            context["quota_feature"].id,
            PlanFeatureUpdate(configuration={"limit": 50}),
        )
        purchase_id = purchase.id
        payment_id = payment.id
        invitation_id = invitation.id

    paid_at = datetime.now(timezone.utc)
    with session_factory() as session:
        payment = PaymentLifecycleService(session).record_verified_outcome(
            payment_id,
            VerifiedPaymentOutcome(
                status="paid",
                paid_at=paid_at,
                provider="test-provider",
                provider_reference="test-reference",
            ),
        )
        assert payment.status == "paid"
        assert payment.provider == "test-provider"
        assert payment.provider_reference == "test-reference"
        purchase = session.get(PersonalPurchase, purchase_id)
        assert purchase.status == "active"
        assert purchase.activated_at.replace(tzinfo=timezone.utc) == paid_at
        assert purchase.expires_at.replace(tzinfo=timezone.utc) == (
            paid_at + timedelta(days=60)
        )

        decision = EntitlementResolver(session).evaluate_feature_access(
            "guest_quota", user_id=owner.id, invitation_id=invitation_id
        )
        assert decision.allowed is True
        assert decision.limit == 3

        retry = PaymentLifecycleService(session).record_verified_outcome(
            payment_id,
            VerifiedPaymentOutcome(status="paid", paid_at=paid_at),
        )
        assert retry.status == "paid"
        assert purchase.status == "active"

        new_purchase = _create_personal_purchase(session, owner, plan.id)
        assert (
            new_purchase.payment.status == "pending"
            and new_purchase.purchased_price == Decimal("12.50")
        )
        new_purchase_model = session.get(PersonalPurchase, new_purchase.id)
        assert new_purchase_model.entitlement_snapshot["features"]["guest_quota"][
            "configuration"
        ] == {"limit": 50}

        refunded = PaymentLifecycleService(session).record_verified_outcome(
            payment_id,
            VerifiedPaymentOutcome(status="refunded"),
        )
        assert refunded.status == "refunded"
        assert purchase.status == "refunded"
        assert not EntitlementResolver(session).evaluate_feature_access(
            "guest_quota", user_id=owner.id, invitation_id=invitation_id
        ).allowed
        assert session.get(PlanFeature, mapping.id).configuration == {"limit": 50}


def test_unassigned_purchase_claim_survives_mapping_removal(billing_context):
    context = billing_context
    with context["session_factory"]() as session:
        created = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        CatalogAdminService(session).delete_plan_feature(
            context["personal_plan"].id, context["feature"].id
        )
        PaymentLifecycleService(session).record_verified_outcome(
            created.payment.id,
            VerifiedPaymentOutcome(
                status="paid", paid_at=datetime.now(timezone.utc)
            ),
        )
        invitation = InvitationService(session).create_personal(
            context["owner"],
            InvitationCreate(
                title="Claim from snapshot",
                template_id=context["premium_template"].id,
            ),
        )
        purchase = session.get(PersonalPurchase, created.id)
        assert purchase.invitation_id == invitation.id
        assert EntitlementResolver(session).evaluate_feature_access(
            "premium_templates",
            user_id=context["owner"].id,
            invitation_id=invitation.id,
        ).allowed


@pytest.mark.parametrize(
    ("outcome_status", "target_status"),
    [
        ("failed", "cancelled"),
        ("expired", "cancelled"),
        ("cancelled", "cancelled"),
    ],
)
def test_non_success_payment_outcomes_do_not_activate_purchase(
    billing_context, outcome_status, target_status
):
    context = billing_context
    with context["session_factory"]() as session:
        purchase_read = _create_personal_purchase(
            session, context["owner"], context["personal_plan"].id
        )
        result = PaymentLifecycleService(session).record_verified_outcome(
            purchase_read.payment.id,
            VerifiedPaymentOutcome(status=outcome_status),
        )
        purchase = session.get(PersonalPurchase, purchase_read.id)
        assert result.status == outcome_status
        assert purchase.status == target_status
        assert purchase.activated_at is None
        assert purchase.expires_at is None
        if outcome_status == "failed":
            with pytest.raises(HTTPException) as invalid_transition:
                PaymentLifecycleService(session).record_verified_outcome(
                    purchase_read.payment.id,
                    VerifiedPaymentOutcome(
                        status="paid", paid_at=datetime.now(timezone.utc)
                    ),
                )
            assert invalid_transition.value.status_code == 409
            session.rollback()
            assert session.get(Payment, purchase_read.payment.id).status == "failed"


def test_personal_purchase_rejects_inactive_unpriced_and_wrong_billing_plans(
    billing_context,
):
    context = billing_context
    with context["session_factory"]() as session:
        inactive = Plan(
            code=f"inactive-{uuid4().hex}",
            name="Inactive",
            billing_type="one_time",
            duration_type="fixed",
            duration_days=30,
            price=Decimal("10.00"),
            currency="USD",
            is_active=False,
        )
        unpriced = Plan(
            code=f"unpriced-{uuid4().hex}",
            name="Unpriced",
            billing_type="one_time",
            duration_type="fixed",
            duration_days=30,
            price=None,
            currency=None,
            is_active=True,
        )
        workspace_plan = Plan(
            code=f"wrong-type-{uuid4().hex}",
            name="Workspace",
            billing_type="subscription",
            duration_type="recurring",
            billing_period_days=30,
            price=Decimal("20.00"),
            currency="USD",
            is_active=True,
        )
        invalid_currency = Plan(
            code=f"invalid-currency-{uuid4().hex}",
            name="Invalid currency plan",
            billing_type="one_time",
            duration_type="fixed",
            duration_days=30,
            price=Decimal("10.00"),
            currency="usd",
            is_active=True,
        )
        session.add_all([inactive, unpriced, workspace_plan, invalid_currency])
        session.commit()

        for invalid_plan in (inactive, unpriced, workspace_plan, invalid_currency):
            with pytest.raises(HTTPException) as rejected:
                _create_personal_purchase(session, context["owner"], invalid_plan.id)
            assert rejected.value.status_code == 409
            session.rollback()


def test_workspace_subscription_requires_authorized_membership_and_paid_payment(
    billing_context,
):
    context = billing_context
    owner = context["owner"]
    outsider = context["outsider"]
    plan = context["workspace_plan"]
    with context["session_factory"]() as session:
        workspace = Workspace(
            name="Billing workspace",
            slug=f"billing-{uuid4().hex}",
            owner_id=owner.id,
            status="active",
        )
        owner_membership = WorkspaceMember(
            workspace=workspace,
            user_id=owner.id,
            role="owner",
            status="active",
        )
        session.add_all([workspace, owner_membership])
        session.commit()

        with pytest.raises(HTTPException) as unauthorized:
            _create_workspace_subscription(session, outsider, workspace.id, plan.id)
        assert unauthorized.value.status_code == 403
        session.rollback()

        read = _create_workspace_subscription(session, owner, workspace.id, plan.id)
        subscription = session.get(Subscription, read.id)
        assert read.status == "pending"
        assert read.current_period_start is None
        assert read.current_period_end is None
        assert read.purchased_price == Decimal("49.00")
        assert read.billing_period_days_snapshot == 30

        denied = EntitlementResolver(session).evaluate_feature_access(
            "premium_templates", workspace_id=workspace.id, user_id=owner.id
        )
        assert denied.allowed is False

        paid_at = datetime.now(timezone.utc)
        PaymentLifecycleService(session).record_verified_outcome(
            read.payment.id,
            VerifiedPaymentOutcome(status="paid", paid_at=paid_at),
        )
        assert subscription.status == "active"
        assert subscription.current_period_start.replace(tzinfo=timezone.utc) == paid_at
        assert subscription.current_period_end.replace(tzinfo=timezone.utc) == (
            paid_at + timedelta(days=30)
        )
        decision = EntitlementResolver(session).evaluate_feature_access(
            "premium_templates", workspace_id=workspace.id, user_id=owner.id
        )
        assert decision.allowed is True

        workspace_mapping = session.scalar(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan.id,
                PlanFeature.feature_id == context["feature"].id,
            )
        )
        CatalogAdminService(session).update_plan_feature(
            plan.id,
            context["feature"].id,
            PlanFeatureUpdate(is_enabled=False),
        )
        assert workspace_mapping.is_enabled is False
        assert EntitlementResolver(session).evaluate_feature_access(
            "premium_templates", workspace_id=workspace.id, user_id=owner.id
        ).allowed

        other_workspace = Workspace(
            name="New snapshot workspace",
            slug=f"new-snapshot-{uuid4().hex}",
            owner_id=owner.id,
            status="active",
        )
        session.add_all(
            [
                other_workspace,
                WorkspaceMember(
                    workspace=other_workspace,
                    user_id=owner.id,
                    role="owner",
                    status="active",
                ),
            ]
        )
        session.commit()
        new_subscription = _create_workspace_subscription(
            session, owner, other_workspace.id, plan.id
        )
        new_model = session.get(Subscription, new_subscription.id)
        assert (
            "premium_templates"
            not in new_model.entitlement_snapshot["features"]
        )


def test_workspace_plan_requires_explicit_billing_period(billing_context):
    context = billing_context
    owner = context["owner"]
    with context["session_factory"]() as session:
        workspace = Workspace(
            name="No period workspace",
            slug=f"no-period-{uuid4().hex}",
            owner_id=owner.id,
            status="active",
        )
        session.add_all(
            [
                workspace,
                WorkspaceMember(
                    workspace=workspace,
                    user_id=owner.id,
                    role="owner",
                    status="active",
                ),
            ]
        )
        no_period_plan = Plan(
            code=f"no-period-{uuid4().hex}",
            name="No period",
            billing_type="subscription",
            duration_type="recurring",
            price=Decimal("20.00"),
            currency="USD",
            is_active=True,
        )
        session.add(no_period_plan)
        session.commit()
        with pytest.raises(HTTPException) as invalid:
            _create_workspace_subscription(
                session, owner, workspace.id, no_period_plan.id
            )
        assert invalid.value.status_code == 409
        session.rollback()


def test_subscription_refund_clears_dates_and_failed_refund_rolls_back(
    billing_context,
):
    context = billing_context
    with context["session_factory"]() as session:
        workspace = _workspace(session, context["owner"])
        created = _create_workspace_subscription(
            session, context["owner"], workspace.id, context["workspace_plan"].id
        )
        paid_at = datetime.now(timezone.utc)
        PaymentLifecycleService(session).record_verified_outcome(
            created.payment.id,
            VerifiedPaymentOutcome(status="paid", paid_at=paid_at),
        )
        active = session.get(Subscription, created.id)
        snapshot = active.entitlement_snapshot
        period_start = active.current_period_start
        period_end = active.current_period_end
        payment = session.get(Payment, created.payment.id)

        def fail_refund_commit(_session, _flush_context, _instances):
            payment.status = "invalid"

        event.listen(session, "before_flush", fail_refund_commit)
        try:
            with pytest.raises(HTTPException) as failed_refund:
                PaymentLifecycleService(session).record_verified_outcome(
                    payment.id,
                    VerifiedPaymentOutcome(status="refunded"),
                )
        finally:
            event.remove(session, "before_flush", fail_refund_commit)
        assert failed_refund.value.status_code == 409

        active = session.get(Subscription, created.id)
        payment = session.get(Payment, created.payment.id)
        assert active.status == "active"
        assert active.current_period_start.replace(tzinfo=timezone.utc) == (
            period_start.replace(tzinfo=timezone.utc)
        )
        assert active.current_period_end.replace(tzinfo=timezone.utc) == (
            period_end.replace(tzinfo=timezone.utc)
        )
        assert payment.status == "paid"

        PaymentLifecycleService(session).record_verified_outcome(
            payment.id,
            VerifiedPaymentOutcome(status="refunded"),
        )
        refunded = session.get(Subscription, created.id)
        payment = session.get(Payment, created.payment.id)
        assert refunded.status == "refunded"
        assert refunded.current_period_start is None
        assert refunded.current_period_end is None
        assert refunded.entitlement_snapshot == snapshot
        assert payment.status == "refunded"


def test_billing_api_is_read_only_for_payment_status_and_owner_scoped(
    billing_context,
):
    context = billing_context
    client = context["client"]
    client.cookies.set("csrf_token", "billing-csrf")
    headers = {"X-CSRF-Token": "billing-csrf"}
    plan_id = str(context["personal_plan"].id)

    missing_csrf = client.post("/api/v1/purchases", json={"plan_id": plan_id})
    assert missing_csrf.status_code == 403

    created = client.post(
        "/api/v1/purchases",
        json={"plan_id": plan_id, "status": "paid", "amount": "0.01"},
        headers=headers,
    )
    assert created.status_code == 422

    created = client.post(
        "/api/v1/purchases",
        json={"plan_id": plan_id},
        headers={**headers, "Idempotency-Key": "purchase-api-idempotency-001"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["purchased_price"] == "12.50"
    assert body["payment"]["status"] == "pending"
    assert "entitlement_snapshot" not in body
    assert "provider_reference" not in body["payment"]

    context["identity"]["user"] = context["outsider"]
    assert client.get(f"/api/v1/purchases/{body['id']}").status_code == 404
    assert client.get(
        f"/api/v1/payments/{body['payment']['id']}"
    ).status_code == 404
    assert client.post(
        f"/api/v1/payments/{body['payment']['id']}/paid",
        headers=headers,
    ).status_code == 404
