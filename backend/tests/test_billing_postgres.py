import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from app.models.feature import Feature
from app.models.payment import Payment
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.billing.services.billing_service import BillingService
from app.modules.billing.services.payment_lifecycle_service import (
    PaymentLifecycleService,
    VerifiedPaymentOutcome,
)
from app.modules.entitlements.schemas.admin_catalog import PlanFeatureUpdate
from app.modules.entitlements.services.catalog_admin_service import (
    CatalogAdminService,
)
from app.modules.invitations.models.invitation import Invitation
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


_DATABASE_URL = os.environ.get("POSTGRES_ENTITLEMENT_TEST_URL")
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL,
    reason="POSTGRES_ENTITLEMENT_TEST_URL is not configured",
)


@pytest.fixture
def postgres_engine():
    if _DATABASE_URL is None:
        pytest.skip("POSTGRES_ENTITLEMENT_TEST_URL is not configured")
    if make_url(_DATABASE_URL).database != "efhaes_wedding_test":
        raise RuntimeError(
            "Billing PostgreSQL tests may only connect to efhaes_wedding_test"
        )
    engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT current_database()"))
        connection.rollback()
    if database != "efhaes_wedding_test":
        engine.dispose()
        raise RuntimeError(
            f"Refusing billing PostgreSQL tests against {database!r}"
        )
    yield engine
    engine.dispose()


def _session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def _personal_fixture(session: Session, *, code: str):
    suffix = uuid4().hex
    user = User(
        name="Billing integration user",
        email=f"billing-{suffix}@example.invalid",
        password_hash="integration-test-only",
    )
    feature = Feature(
        code=f"feature_{suffix}",
        name="Snapshot feature",
        feature_type="limit",
        is_active=True,
    )
    plan = Plan(
        code=f"{code}-{suffix}",
        name="Integration personal plan",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=60,
        price=Decimal("10.00"),
        currency="USD",
        is_active=True,
    )
    template = Template(
        name="Integration template",
        slug=f"template-{suffix}",
        category="test",
        access_level="free",
        configuration={},
        status="published",
        is_active=True,
    )
    session.add_all([user, feature, plan, template])
    session.flush()
    mapping = PlanFeature(
        plan_id=plan.id,
        feature_id=feature.id,
        is_enabled=True,
        configuration={"limit": 4},
    )
    invitation = Invitation(
        owner_id=user.id,
        template_id=template.id,
        title="Integration invitation",
        slug=f"invitation-{suffix}",
        status="draft",
    )
    session.add_all([mapping, invitation])
    session.commit()
    return user, feature, plan, template, invitation, mapping


def _cleanup_personal(engine, ids: dict[str, object]) -> None:
    with Session(engine) as session:
        session.execute(delete(Payment).where(Payment.id == ids.get("payment_id")))
        session.execute(
            delete(PersonalPurchase).where(
                PersonalPurchase.id == ids.get("purchase_id")
            )
        )
        session.execute(
            delete(Invitation).where(Invitation.id == ids.get("invitation_id"))
        )
        session.execute(
            delete(PlanFeature).where(PlanFeature.id == ids.get("mapping_id"))
        )
        session.execute(
            delete(Template).where(Template.id == ids.get("template_id"))
        )
        session.execute(delete(Feature).where(Feature.id == ids.get("feature_id")))
        session.execute(delete(Plan).where(Plan.id == ids.get("plan_id")))
        session.execute(delete(User).where(User.id == ids.get("user_id")))
        session.commit()


def test_postgres_personal_snapshot_and_catalog_update_are_transactional(
    postgres_engine,
):
    factory = _session_factory(postgres_engine)
    ids: dict[str, object] = {}
    try:
        with factory() as session:
            user, feature, plan, template, invitation, mapping = _personal_fixture(
                session, code="pg-personal"
            )
            ids.update(
                user_id=user.id,
                feature_id=feature.id,
                plan_id=plan.id,
                template_id=template.id,
                invitation_id=invitation.id,
                mapping_id=mapping.id,
            )
            purchase = BillingService(session).create_personal_purchase(user, plan.id)
            ids.update(
                purchase_id=purchase.id,
                payment_id=purchase.payment.id,
            )
            stored = session.get(PersonalPurchase, purchase.id)
            stored.invitation_id = invitation.id
            session.commit()
            assert stored.status == "pending"
            assert stored.entitlement_snapshot["features"][feature.code][
                "configuration"
            ] == {"limit": 4}

            payment = PaymentLifecycleService(session).record_verified_outcome(
                purchase.payment.id,
                VerifiedPaymentOutcome(
                    status="paid", paid_at=datetime.now(timezone.utc)
                ),
            )
            assert payment.status == "paid"
            CatalogAdminService(session).update_plan_feature(
                plan.id,
                feature.id,
                PlanFeatureUpdate(configuration={"limit": 99}),
            )
            assert stored.entitlement_snapshot["features"][feature.code][
                "configuration"
            ] == {"limit": 4}
    finally:
        if ids:
            _cleanup_personal(postgres_engine, ids)


def test_postgres_concurrent_purchase_and_mapping_change_snapshot_is_whole(
    postgres_engine,
):
    factory = _session_factory(postgres_engine)
    ids: dict[str, object] = {}
    try:
        with factory() as session:
            user, feature, plan, template, invitation, mapping = _personal_fixture(
                session, code="pg-race-personal"
            )
            ids.update(
                user_id=user.id,
                feature_id=feature.id,
                plan_id=plan.id,
                template_id=template.id,
                invitation_id=invitation.id,
                mapping_id=mapping.id,
            )
            user_id, feature_id, plan_id = user.id, feature.id, plan.id

        barrier = Barrier(2)

        def create_purchase():
            with factory() as session:
                user = session.get(User, user_id)
                barrier.wait(timeout=10)
                return BillingService(session).create_personal_purchase(
                    user, plan_id
                )

        def change_mapping():
            with factory() as session:
                barrier.wait(timeout=10)
                CatalogAdminService(session).update_plan_feature(
                    plan_id,
                    feature_id,
                    PlanFeatureUpdate(configuration={"limit": 9}),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            purchase_future = pool.submit(create_purchase)
            mapping_future = pool.submit(change_mapping)
            purchase = purchase_future.result(timeout=30)
            ids.update(
                purchase_id=purchase.id,
                payment_id=purchase.payment.id,
            )
            mapping_future.result(timeout=30)
        with factory() as session:
            stored = session.get(PersonalPurchase, purchase.id)
            configuration = stored.entitlement_snapshot["features"][
                session.get(Feature, feature_id).code
            ]["configuration"]
            assert configuration in ({"limit": 4}, {"limit": 9})
            current_mapping = session.get(PlanFeature, mapping.id)
            assert current_mapping.configuration == {"limit": 9}
    finally:
        if ids:
            _cleanup_personal(postgres_engine, ids)


def test_postgres_concurrent_subscription_and_mapping_change_snapshot_is_whole(
    postgres_engine,
):
    factory = _session_factory(postgres_engine)
    ids: dict[str, object] = {}
    try:
        suffix = uuid4().hex
        user = User(
            name="Subscription race user",
            email=f"subscription-race-{suffix}@example.invalid",
            password_hash="integration-test-only",
        )
        feature = Feature(
            code=f"subscription_feature_{suffix}",
            name="Subscription feature",
            feature_type="limit",
            is_active=True,
        )
        plan = Plan(
            code=f"pg-race-subscription-{suffix}",
            name="Integration workspace plan",
            billing_type="subscription",
            duration_type="recurring",
            billing_period_days=30,
            price=Decimal("50.00"),
            currency="USD",
            is_active=True,
        )
        workspace = Workspace(
            name="Subscription race workspace",
            slug=f"subscription-race-{suffix}",
            owner_id=user.id,
            status="active",
        )
        membership = WorkspaceMember(
            workspace=workspace,
            user_id=user.id,
            role="owner",
            status="active",
        )
        with factory() as session:
            session.add_all([user, feature, plan, workspace, membership])
            session.flush()
            mapping = PlanFeature(
                plan_id=plan.id,
                feature_id=feature.id,
                is_enabled=True,
                configuration={"limit": 4},
            )
            session.add(mapping)
            session.commit()
        ids.update(
            user_id=user.id,
            feature_id=feature.id,
            plan_id=plan.id,
            workspace_id=workspace.id,
            membership_id=membership.id,
            mapping_id=mapping.id,
        )
        user_id, feature_id, plan_id, workspace_id = (
            user.id,
            feature.id,
            plan.id,
            workspace.id,
        )
        barrier = Barrier(2)

        def create_subscription():
            with factory() as session:
                user_row = session.get(User, user_id)
                barrier.wait(timeout=10)
                return BillingService(session).create_workspace_subscription(
                    user_row, workspace_id, plan_id
                )

        def change_mapping():
            with factory() as session:
                barrier.wait(timeout=10)
                CatalogAdminService(session).update_plan_feature(
                    plan_id,
                    feature_id,
                    PlanFeatureUpdate(configuration={"limit": 9}),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            subscription_future = pool.submit(create_subscription)
            mapping_future = pool.submit(change_mapping)
            created = subscription_future.result(timeout=30)
            ids.update(
                subscription_id=created.id,
                payment_id=created.payment.id,
            )
            mapping_future.result(timeout=30)
        with factory() as session:
            stored = session.get(Subscription, created.id)
            configuration = stored.entitlement_snapshot["features"][
                session.get(Feature, feature_id).code
            ]["configuration"]
            assert configuration in ({"limit": 4}, {"limit": 9})
            current_mapping = session.get(PlanFeature, mapping.id)
            assert current_mapping.configuration == {"limit": 9}
    finally:
        if ids:
            with Session(postgres_engine) as session:
                session.execute(
                    delete(Payment).where(Payment.id == ids.get("payment_id"))
                )
                session.execute(
                    delete(Subscription).where(
                        Subscription.id == ids.get("subscription_id")
                    )
                )
                session.execute(
                    delete(PlanFeature).where(
                        PlanFeature.id == ids.get("mapping_id")
                    )
                )
                session.execute(
                    delete(WorkspaceMember).where(
                        WorkspaceMember.id == ids.get("membership_id")
                    )
                )
                session.execute(
                    delete(Workspace).where(
                        Workspace.id == ids.get("workspace_id")
                    )
                )
                session.execute(
                    delete(Feature).where(Feature.id == ids.get("feature_id"))
                )
                session.execute(
                    delete(Plan).where(Plan.id == ids.get("plan_id"))
                )
                session.execute(
                    delete(User).where(User.id == ids.get("user_id"))
                )
                session.commit()


def test_postgres_concurrent_payment_success_activates_once(postgres_engine):
    factory = _session_factory(postgres_engine)
    ids: dict[str, object] = {}
    try:
        with factory() as session:
            user, feature, plan, template, invitation, mapping = _personal_fixture(
                session, code="pg-race-payment"
            )
            ids.update(
                user_id=user.id,
                feature_id=feature.id,
                plan_id=plan.id,
                template_id=template.id,
                invitation_id=invitation.id,
                mapping_id=mapping.id,
            )
            purchase = BillingService(session).create_personal_purchase(user, plan.id)
            ids.update(
                purchase_id=purchase.id,
                payment_id=purchase.payment.id,
            )

        barrier = Barrier(2)
        paid_at = datetime.now(timezone.utc)

        def settle():
            with factory() as session:
                barrier.wait(timeout=10)
                return PaymentLifecycleService(session).record_verified_outcome(
                    ids["payment_id"],
                    VerifiedPaymentOutcome(status="paid", paid_at=paid_at),
                ).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(settle),
                pool.submit(settle),
            ]
            assert [future.result(timeout=30) for future in results] == [
                "paid",
                "paid",
            ]
        with factory() as session:
            purchase = session.get(PersonalPurchase, ids["purchase_id"])
            payments = session.scalars(
                select(Payment).where(
                    Payment.personal_purchase_id == purchase.id,
                    Payment.status == "paid",
                )
            ).all()
            assert purchase.status == "active"
            assert len(payments) == 1
            assert purchase.activated_at is not None
    finally:
        if ids:
            _cleanup_personal(postgres_engine, ids)
