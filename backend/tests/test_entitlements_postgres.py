import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import make_url

from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.invitation_section import InvitationSection
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.entitlements.services.entitlement_resolver import EntitlementResolver
from app.modules.entitlements.schemas.admin_catalog import (
    FeatureUpdate,
    PlanFeatureCreate,
    PlanFeatureUpdate,
    PlanUpdate,
)
from app.modules.entitlements.services.catalog_admin_service import (
    CatalogAdminService,
)
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.schemas.invitation import InvitationCreate
from app.modules.invitations.services.invitation_service import InvitationService
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


_DATABASE_URL = os.environ.get("POSTGRES_ENTITLEMENT_TEST_URL")
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL,
    reason="POSTGRES_ENTITLEMENT_TEST_URL is not configured",
)


@pytest.fixture
def postgres_session():
    if _DATABASE_URL is None:
        pytest.skip("POSTGRES_ENTITLEMENT_TEST_URL is not configured")
    if make_url(_DATABASE_URL).database != "efhaes_wedding_test":
        raise RuntimeError(
            "PostgreSQL entitlement tests may only connect to efhaes_wedding_test"
        )

    engine = create_engine(_DATABASE_URL)
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT current_database()"))
        if database != "efhaes_wedding_test":
            engine.dispose()
            raise RuntimeError(
                f"Refusing PostgreSQL entitlement tests against {database!r}"
            )
        connection.rollback()
        outer_transaction = connection.begin()
        factory = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            session.close()
            outer_transaction.rollback()
    engine.dispose()


def _base_data(session: Session):
    unique = uuid4().hex
    owner = User(
        name="Entitlement owner",
        email=f"owner-{unique}@example.invalid",
        password_hash="test-fixture-not-a-login-credential",
    )
    other_user = User(
        name="Other entitlement user",
        email=f"other-{unique}@example.invalid",
        password_hash="test-fixture-not-a-login-credential",
    )
    premium_template = Template(
        name="Premium fixture",
        slug=f"premium-{unique}",
        category="test",
        access_level="premium",
        configuration={"default_sections": []},
        status="published",
        is_active=True,
    )
    free_template = Template(
        name="Free fixture",
        slug=f"free-{unique}",
        category="test",
        access_level="free",
        configuration={"default_sections": []},
        status="published",
        is_active=True,
    )
    limit_feature = Feature(
        code=f"guest_limit_{unique}",
        name="Guest limit fixture",
        feature_type="limit",
    )
    premium_template_feature = Feature(
        code="premium_templates",
        name="Premium templates",
        feature_type="boolean",
    )
    personal_plan = Plan(
        code=f"personal-{unique}",
        name="Personal purchase fixture",
        billing_type="one_time",
        duration_type="fixed",
        price=1,
        currency="USD",
        duration_days=365,
        is_active=True,
    )
    workspace_plan = Plan(
        code=f"workspace-{unique}",
        name="Workspace subscription fixture",
        billing_type="subscription",
        duration_type="recurring",
        price=1,
        currency="USD",
        is_active=True,
    )
    session.add_all(
        [
            owner,
            other_user,
            premium_template,
            free_template,
            limit_feature,
            premium_template_feature,
            personal_plan,
            workspace_plan,
        ]
    )
    session.flush()
    session.add_all(
        [
            PlanFeature(
                plan_id=personal_plan.id,
                feature_id=premium_template_feature.id,
                configuration={},
            ),
            PlanFeature(
                plan_id=personal_plan.id,
                feature_id=limit_feature.id,
                configuration={"limit": 5},
            ),
            PlanFeature(
                plan_id=workspace_plan.id,
                feature_id=premium_template_feature.id,
                configuration={},
            ),
        ]
    )
    session.flush()
    session.commit()
    return {
        "owner": owner,
        "other_user": other_user,
        "premium_template": premium_template,
        "free_template": free_template,
        "premium_feature": premium_template_feature,
        "limit_feature": limit_feature,
        "personal_plan": personal_plan,
        "workspace_plan": workspace_plan,
    }


def _active_purchase(user_id, plan_id, *, invitation_id=None):
    now = datetime.now(timezone.utc)
    return PersonalPurchase(
        user_id=user_id,
        invitation_id=invitation_id,
        plan_id=plan_id,
        status="active",
        activated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(days=30),
    )


def test_postgres_catalog_updates_preserve_existing_entitlements(postgres_session):
    session = postgres_session
    data = _base_data(session)
    owner = data["owner"]
    now = datetime.now(timezone.utc)
    invitation = Invitation(
        owner_id=owner.id,
        template_id=data["free_template"].id,
        title="Catalog update fixture",
        slug=f"catalog-update-{uuid4().hex}",
        status="draft",
    )
    workspace = Workspace(
        name="Catalog update workspace",
        slug=f"catalog-workspace-{uuid4().hex}",
        owner_id=owner.id,
        status="active",
    )
    membership = WorkspaceMember(
        workspace=workspace,
        user_id=owner.id,
        role="owner",
        status="active",
    )
    session.add_all([invitation, workspace, membership])
    session.flush()
    purchase = PersonalPurchase(
        user_id=owner.id,
        invitation_id=invitation.id,
        plan_id=data["personal_plan"].id,
        status="active",
        activated_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=29),
        duration_type_snapshot="fixed",
        duration_days_snapshot=30,
        purchased_price=1,
        purchased_currency="USD",
    )
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=data["workspace_plan"].id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
        cancel_at_period_end=False,
    )
    session.add_all([purchase, subscription])
    session.commit()

    service = CatalogAdminService(session)
    service.update_plan(
        data["personal_plan"].id,
        PlanUpdate(price=Decimal("35.00"), duration_days=90),
    )
    service.update_plan(
        data["personal_plan"].id,
        PlanUpdate(is_active=False),
    )
    service.update_plan(
        data["workspace_plan"].id,
        PlanUpdate(price=Decimal("49.00"), is_active=False),
    )

    assert purchase.duration_days_snapshot == 30
    assert purchase.purchased_price == Decimal("1.00")
    assert purchase.expires_at == now + timedelta(days=29)
    resolver = EntitlementResolver(session)
    personal = resolver.evaluate_feature_access(
        "premium_templates",
        user_id=owner.id,
        invitation_id=invitation.id,
    )
    workspace_decision = resolver.evaluate_feature_access(
        "premium_templates",
        user_id=owner.id,
        workspace_id=workspace.id,
    )
    assert personal.allowed is True
    assert personal.expires_at == purchase.expires_at
    assert workspace_decision.allowed is True
    assert workspace_decision.expires_at == subscription.current_period_end

    with pytest.raises(HTTPException) as mapping_change:
        service.update_plan_feature(
            data["personal_plan"].id,
            data["premium_feature"].id,
            PlanFeatureUpdate(is_enabled=False),
        )
    assert mapping_change.value.status_code == 409
    with pytest.raises(HTTPException) as feature_deactivation:
        service.update_feature(
            data["premium_feature"].id,
            FeatureUpdate(is_active=False),
        )
    assert feature_deactivation.value.status_code == 409


def test_postgres_duplicate_catalog_mapping_rolls_back(postgres_session):
    session = postgres_session
    unique = uuid4().hex
    plan = Plan(
        code=f"catalog-plan-{unique}",
        name="Catalog mapping fixture",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=30,
        price=None,
        currency=None,
        is_active=False,
    )
    feature = Feature(
        code=f"catalog-feature-{unique}",
        name="Catalog mapping feature",
        feature_type="boolean",
        is_active=True,
    )
    session.add_all([plan, feature])
    session.flush()
    session.add(
        PlanFeature(
            plan_id=plan.id,
            feature_id=feature.id,
            configuration={},
            is_enabled=False,
        )
    )
    session.commit()

    with pytest.raises(HTTPException) as duplicate:
        CatalogAdminService(session).add_plan_feature(
            plan.id,
            PlanFeatureCreate(feature_id=feature.id, is_enabled=False),
        )
    assert duplicate.value.status_code == 409
    assert session.scalar(
        select(PlanFeature.id).where(
            PlanFeature.plan_id == plan.id,
            PlanFeature.feature_id == feature.id,
        )
    ) is not None


def test_postgres_admin_mapping_is_used_by_new_entitlement(postgres_session):
    session = postgres_session
    data = _base_data(session)
    owner = data["owner"]
    plan = Plan(
        code=f"admin-mapped-plan-{uuid4().hex}",
        name="Admin mapped plan",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=60,
        price=Decimal("10.00"),
        currency="USD",
        is_active=True,
    )
    session.add(plan)
    session.commit()
    mapping = CatalogAdminService(session).add_plan_feature(
        plan.id,
        PlanFeatureCreate(
            feature_id=data["premium_feature"].id,
            is_enabled=True,
            configuration={},
        ),
    )
    assert mapping.is_enabled is True

    invitation = Invitation(
        owner_id=owner.id,
        template_id=data["free_template"].id,
        title="Admin catalog entitlement",
        slug=f"admin-entitlement-{uuid4().hex}",
        status="draft",
    )
    session.add(invitation)
    session.flush()
    now = datetime.now(timezone.utc)
    purchase = PersonalPurchase(
        user_id=owner.id,
        invitation_id=invitation.id,
        plan_id=plan.id,
        status="active",
        activated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(days=60),
        duration_type_snapshot="fixed",
        duration_days_snapshot=60,
        purchased_price=Decimal("10.00"),
        purchased_currency="USD",
    )
    session.add(purchase)
    session.commit()

    decision = EntitlementResolver(session).evaluate_feature_access(
        "premium_templates",
        user_id=owner.id,
        invitation_id=invitation.id,
    )
    assert decision.allowed is True
    assert decision.plan_code == plan.code
    assert decision.expires_at == purchase.expires_at


def test_postgres_personal_scope_and_single_claim_lifecycle(postgres_session):
    session = postgres_session
    data = _base_data(session)
    owner = data["owner"]
    other_user = data["other_user"]

    invalid_invitation = Invitation(
        owner_id=owner.id,
        template_id=data["free_template"].id,
        title="Scope fixture",
        slug=f"scope-{uuid4().hex}",
        status="draft",
    )
    session.add(invalid_invitation)
    session.flush()
    bad_purchase = _active_purchase(
        other_user.id,
        data["personal_plan"].id,
        invitation_id=invalid_invitation.id,
    )
    with pytest.raises(IntegrityError) as error:
        with session.begin_nested():
            session.add(bad_purchase)
            session.flush()
    assert error.value.orig.diag.constraint_name == (
        "fk_personal_purchases_invitation_owner"
    )
    session.commit()

    pending = PersonalPurchase(
        user_id=owner.id,
        invitation_id=None,
        plan_id=data["personal_plan"].id,
        status="pending",
    )
    active_pending_payment = _active_purchase(owner.id, data["personal_plan"].id)
    active_failed_payment = _active_purchase(owner.id, data["personal_plan"].id)
    session.add_all([pending, active_pending_payment, active_failed_payment])
    session.flush()
    session.add_all(
        [
            Payment(
                personal_purchase_id=active_pending_payment.id,
                provider="fixture",
                amount=1,
                currency="USD",
                status="pending",
            ),
            Payment(
                personal_purchase_id=active_failed_payment.id,
                provider="fixture",
                amount=1,
                currency="USD",
                status="failed",
            ),
        ]
    )
    session.commit()

    for title in (
        "Pending purchase must not grant",
        "Pending payment must not grant",
        "Failed payment must not grant",
    ):
        with pytest.raises(HTTPException) as denied:
            InvitationService(session).create_personal(
                owner,
                InvitationCreate(
                    title=title,
                    template_id=data["premium_template"].id,
                ),
            )
        assert denied.value.status_code == 403
    assert session.scalar(
        select(Invitation.id).where(Invitation.slug == "pending-purchase-must-not-grant")
    ) is None

    # This fixture is an already-active server-authorized purchase state, not
    # a simulated payment confirmation or a Payment status update.
    active = _active_purchase(owner.id, data["personal_plan"].id)
    session.add(active)
    session.commit()

    created = InvitationService(session).create_personal(
        owner,
        InvitationCreate(
            title="Premium invitation",
            template_id=data["premium_template"].id,
        ),
    )
    assert active.invitation_id == created.id
    assert created.owner_id == owner.id

    with pytest.raises(HTTPException) as cross_invitation_denied:
        InvitationService(session).create_personal(
            owner,
            InvitationCreate(
                title="Second premium invitation",
                template_id=data["premium_template"].id,
            ),
        )
    assert cross_invitation_denied.value.status_code == 403

    with pytest.raises(HTTPException) as cross_user_denied:
        InvitationService(session).create_personal(
            other_user,
            InvitationCreate(
                title="Other user's premium invitation",
                template_id=data["premium_template"].id,
            ),
        )
    assert cross_user_denied.value.status_code == 403

    assert session.scalar(
        select(PersonalPurchase.id).where(
            PersonalPurchase.id == pending.id,
            PersonalPurchase.invitation_id.is_(None),
        )
    ) == pending.id
    assert session.scalar(
        select(PersonalPurchase.id).where(
            PersonalPurchase.id == active.id,
            PersonalPurchase.invitation_id == created.id,
        )
    ) == active.id


def test_postgres_workspace_subscription_requires_active_membership(postgres_session):
    session = postgres_session
    data = _base_data(session)
    owner = data["owner"]
    other_user = data["other_user"]
    workspace = Workspace(
        name="Entitlement workspace",
        slug=f"workspace-{uuid4().hex}",
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
    session.flush()
    session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=data["workspace_plan"].id,
            status="active",
            current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            cancel_at_period_end=False,
        )
    )
    session.commit()

    resolver = EntitlementResolver(session)
    owner_decision = resolver.evaluate_feature_access(
        "premium_templates",
        workspace_id=workspace.id,
        user_id=owner.id,
    )
    outsider_decision = resolver.evaluate_feature_access(
        "premium_templates",
        workspace_id=workspace.id,
        user_id=other_user.id,
    )
    assert owner_decision.allowed is True
    assert owner_decision.source == "workspace_subscription"
    assert outsider_decision.allowed is False
    assert outsider_decision.reason == "no_active_workspace_membership"

    created = InvitationService(session).create_workspace(
        workspace.id,
        owner_membership,
        InvitationCreate(
            title="Workspace premium invitation",
            template_id=data["premium_template"].id,
        ),
        None,
    )
    assert created.workspace_id == workspace.id


def test_postgres_override_expiry_revocation_and_limit_feature(postgres_session):
    session = postgres_session
    data = _base_data(session)
    owner = data["owner"]
    invitation = Invitation(
        owner_id=owner.id,
        template_id=data["free_template"].id,
        title="Entitlement evaluation fixture",
        slug=f"evaluation-{uuid4().hex}",
        status="draft",
    )
    session.add(invitation)
    session.flush()
    purchase = _active_purchase(
        owner.id,
        data["personal_plan"].id,
        invitation_id=invitation.id,
    )
    session.add(purchase)
    now = datetime.now(timezone.utc)
    expired_override = EntitlementOverride(
        invitation_id=invitation.id,
        feature_id=data["premium_feature"].id,
        is_enabled=True,
        expires_at=now - timedelta(seconds=1),
    )
    session.add(expired_override)
    session.commit()

    resolver = EntitlementResolver(session)
    expired = resolver.evaluate_feature_access(
        "premium_templates",
        user_id=owner.id,
        invitation_id=invitation.id,
    )
    assert expired.allowed is True
    assert expired.source == "personal_purchase"

    revoked_override = EntitlementOverride(
        invitation_id=invitation.id,
        feature_id=data["premium_feature"].id,
        is_enabled=False,
        expires_at=now + timedelta(days=1),
    )
    session.add(revoked_override)
    session.commit()
    revoked = resolver.evaluate_feature_access(
        "premium_templates",
        user_id=owner.id,
        invitation_id=invitation.id,
    )
    assert revoked.allowed is False
    assert revoked.reason == "disabled_by_override"

    limit = resolver.evaluate_feature_access(
        data["limit_feature"].code,
        user_id=owner.id,
        invitation_id=invitation.id,
    )
    assert limit.allowed is True
    assert limit.limit == 5

    with pytest.raises(HTTPException) as unknown:
        resolver.require_feature_access(
            "unconfigured_feature",
            user_id=owner.id,
            invitation_id=invitation.id,
        )
    assert unknown.value.status_code == 403


def test_postgres_concurrent_requests_claim_personal_purchase_once():
    if _DATABASE_URL is None:
        pytest.skip("POSTGRES_ENTITLEMENT_TEST_URL is not configured")
    if make_url(_DATABASE_URL).database != "efhaes_wedding_test":
        raise RuntimeError(
            "PostgreSQL entitlement tests may only connect to efhaes_wedding_test"
        )

    engine = create_engine(_DATABASE_URL)
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT current_database()"))
        connection.rollback()
    if database != "efhaes_wedding_test":
        engine.dispose()
        raise RuntimeError(
            f"Refusing PostgreSQL entitlement tests against {database!r}"
        )

    factory = sessionmaker(engine, expire_on_commit=False)
    data = None
    try:
        with factory() as setup:
            data = _base_data(setup)
            purchase = _active_purchase(
                data["owner"].id,
                data["personal_plan"].id,
            )
            setup.add(purchase)
            setup.commit()
            owner_id = data["owner"].id
            template_id = data["premium_template"].id

        barrier = Barrier(2)

        def create_invitation(title: str) -> tuple[str, int | str]:
            with factory() as session:
                owner = session.get(User, owner_id)
                assert owner is not None
                barrier.wait(timeout=10)
                try:
                    invitation = InvitationService(session).create_personal(
                        owner,
                        InvitationCreate(
                            title=title,
                            template_id=template_id,
                        ),
                    )
                except HTTPException as error:
                    return "denied", error.status_code
                return "created", str(invitation.id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    create_invitation,
                    ("Concurrent premium A", "Concurrent premium B"),
                )
            )
        created = [value for outcome, value in results if outcome == "created"]
        denied = [value for outcome, value in results if outcome == "denied"]
        assert len(created) == 1
        assert denied == [403]

        with factory() as verify:
            claimed = verify.scalar(
                select(PersonalPurchase.invitation_id).where(
                    PersonalPurchase.user_id == owner_id,
                )
            )
            assert str(claimed) == created[0]
    finally:
        if data is not None:
            with factory() as cleanup:
                owner_id = data["owner"].id
                plan_id = data["personal_plan"].id
                workspace_plan_id = data["workspace_plan"].id
                premium_template_id = data["premium_template"].id
                free_template_id = data["free_template"].id
                limit_feature_id = data["limit_feature"].id
                premium_feature_id = data["premium_feature"].id
                cleanup.execute(
                    delete(InvitationSection).where(
                        InvitationSection.invitation_id.in_(
                            select(Invitation.id).where(
                                Invitation.owner_id == owner_id
                            )
                        )
                    )
                )
                cleanup.execute(
                    delete(PersonalPurchase).where(
                        PersonalPurchase.user_id == owner_id
                    )
                )
                cleanup.execute(
                    delete(Invitation).where(Invitation.owner_id == owner_id)
                )
                cleanup.execute(
                    delete(PlanFeature).where(
                        PlanFeature.plan_id.in_([plan_id, workspace_plan_id])
                    )
                )
                cleanup.execute(
                    delete(Feature).where(
                        Feature.id.in_([limit_feature_id, premium_feature_id])
                    )
                )
                cleanup.execute(delete(Plan).where(Plan.id.in_([plan_id, workspace_plan_id])))
                cleanup.execute(
                    delete(Template).where(
                        Template.id.in_([premium_template_id, free_template_id])
                    )
                )
                cleanup.execute(delete(User).where(User.id == owner_id))
                cleanup.execute(
                    delete(User).where(User.id == data["other_user"].id)
                )
                cleanup.commit()
        engine.dispose()
