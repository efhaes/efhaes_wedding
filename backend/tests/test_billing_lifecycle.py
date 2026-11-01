from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, get_db
from app.database.base import Base
from app.main import app
from app.models.client import Client
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


def test_personal_purchase_snapshots_features_and_settlement_is_idempotent(
    billing_context,
):
    context = billing_context
    owner = context["owner"]
    plan = context["personal_plan"]
    mapping = context["quota_mapping"]
    session_factory = context["session_factory"]

    with session_factory() as session:
        purchase_read = BillingService(session).create_personal_purchase(owner, plan.id)
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

        new_purchase = BillingService(session).create_personal_purchase(owner, plan.id)
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
        created = BillingService(session).create_personal_purchase(
            context["owner"], context["personal_plan"].id
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
        purchase_read = BillingService(session).create_personal_purchase(
            context["owner"], context["personal_plan"].id
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
                BillingService(session).create_personal_purchase(
                    context["owner"], invalid_plan.id
                )
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
            BillingService(session).create_workspace_subscription(
                outsider, workspace.id, plan.id
            )
        assert unauthorized.value.status_code == 403
        session.rollback()

        read = BillingService(session).create_workspace_subscription(
            owner, workspace.id, plan.id
        )
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
        new_subscription = BillingService(session).create_workspace_subscription(
            owner, other_workspace.id, plan.id
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
            BillingService(session).create_workspace_subscription(
                owner, workspace.id, no_period_plan.id
            )
        assert invalid.value.status_code == 409
        session.rollback()


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
        "/api/v1/purchases", json={"plan_id": plan_id}, headers=headers
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
