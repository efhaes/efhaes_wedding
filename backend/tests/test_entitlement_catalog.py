from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.feature import Feature
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.modules.entitlements.schemas.catalog import PlanConfiguration
from app.modules.entitlements.seed import seed_catalog


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def catalog_session() -> Session:
    engine: Engine = create_engine("sqlite://")
    tables = [Plan.__table__, Feature.__table__, PlanFeature.__table__]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _plan_config(**overrides) -> PlanConfiguration:
    values = {
        "code": "premium_personal",
        "name": "Premium Personal",
        "billing_type": "one_time",
        "duration_type": "fixed",
        "duration_days": 90,
        "price": Decimal("12.50"),
        "currency": "USD",
        "is_active": True,
    }
    values.update(overrides)
    return PlanConfiguration(**values)


def test_plan_configuration_accepts_fixed_lifetime_and_pending():
    assert _plan_config(duration_days=30).duration_days == 30
    assert _plan_config(duration_days=90).duration_days == 90
    assert _plan_config(duration_days=180).duration_days == 180
    assert _plan_config(duration_days=365).duration_days == 365
    lifetime = _plan_config(duration_type="lifetime", duration_days=None)
    assert lifetime.duration_type == "lifetime"
    pending = _plan_config(
        duration_type="pending",
        duration_days=None,
        price=None,
        currency=None,
        is_active=False,
    )
    assert pending.duration_type == "pending"
    zero_price_with_currency = _plan_config(
        price=Decimal("0"),
        currency="USD",
    )
    assert zero_price_with_currency.currency == "USD"
    workspace = PlanConfiguration(
        code="workspace",
        name="Workspace",
        billing_type="subscription",
        duration_type="recurring",
    )
    assert workspace.duration_type == "recurring"
    configured_workspace = PlanConfiguration(
        code="configured_workspace",
        name="Configured Workspace",
        billing_type="subscription",
        duration_type="recurring",
        price=Decimal("10.00"),
        currency="USD",
        billing_period_days=30,
        is_active=True,
    )
    assert configured_workspace.billing_period_days == 30


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_type": "fixed", "duration_days": None},
        {"duration_type": "fixed", "duration_days": 0},
        {"duration_type": "lifetime", "duration_days": 30},
        {"duration_type": "recurring", "duration_days": None},
        {"billing_type": "subscription", "duration_type": "fixed", "duration_days": 30},
        {"price": None, "currency": "USD"},
        {"price": Decimal("10"), "currency": None},
        {
            "billing_type": "subscription",
            "duration_type": "recurring",
            "duration_days": None,
            "is_active": True,
        },
        {"billing_period_days": 30},
    ],
)
def test_plan_configuration_rejects_ambiguous_values(overrides):
    with pytest.raises(ValidationError):
        _plan_config(**overrides)


def test_plan_database_constraints_reject_invalid_duration_and_price(catalog_session):
    session = catalog_session
    valid_legacy_free_plan = Plan(
        code="legacy_free",
        name="Legacy Free",
        billing_type="one_time",
        duration_type="lifetime",
        price=Decimal("0"),
        currency="USD",
    )
    session.add(valid_legacy_free_plan)
    session.flush()

    invalid_fixed = Plan(
        code="invalid_fixed",
        name="Invalid fixed",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=None,
        price=None,
        currency=None,
    )
    session.add(invalid_fixed)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    invalid_price = Plan(
        code="invalid_price",
        name="Invalid price",
        billing_type="one_time",
        duration_type="lifetime",
        price=Decimal("10"),
        currency=None,
    )
    session.add(invalid_price)
    with pytest.raises(IntegrityError):
        session.flush()


def test_plan_feature_mapping_is_unique_and_enableable(catalog_session):
    session = catalog_session
    plan = Plan(
        code="workspace_monthly",
        name="Workspace",
        billing_type="subscription",
        duration_type="recurring",
        price=None,
        currency=None,
        is_active=False,
    )
    feature = Feature(
        code="premium_templates",
        name="Premium templates",
        feature_type="boolean",
        is_active=True,
    )
    session.add_all([plan, feature])
    session.flush()
    mapping = PlanFeature(
        plan_id=plan.id,
        feature_id=feature.id,
        configuration={},
        is_enabled=False,
    )
    session.add(mapping)
    session.flush()
    assert mapping.is_enabled is False
    assert feature.code == "premium_templates"

    session.add(
        PlanFeature(
            plan_id=plan.id,
            feature_id=feature.id,
            configuration={},
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_feature_codes_are_unique(catalog_session):
    session = catalog_session
    session.add(
        Feature(code="premium_templates", name="Premium", feature_type="boolean")
    )
    session.flush()
    session.add(
        Feature(code="premium_templates", name="Duplicate", feature_type="boolean")
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_catalog_seed_is_idempotent_and_preserves_admin_edits(catalog_session):
    session = catalog_session
    first = seed_catalog(session)
    session.flush()
    free = session.scalar(select(Plan).where(Plan.code == "free"))
    assert free is not None
    assert free.price is None
    assert free.duration_type == "pending"
    assert free.is_active is False
    premium = session.scalar(
        select(Plan).where(Plan.code == "premium_personal")
    )
    assert premium is not None
    assert premium.price is None
    assert premium.duration_type == "pending"
    assert premium.is_active is False
    premium.name = "Admin configured Premium"
    premium.price = Decimal("25.00")
    premium.currency = "USD"
    premium.duration_type = "fixed"
    premium.duration_days = 180
    session.flush()

    second = seed_catalog(session)
    session.flush()
    updated = session.scalar(
        select(Plan).where(Plan.code == "premium_personal")
    )
    assert first == {"plans_added": 3, "features_added": 1}
    assert second == {"plans_added": 0, "features_added": 0}
    assert session.scalar(select(func.count()).select_from(Plan)) == 3
    assert session.scalar(select(func.count()).select_from(Feature)) == 1
    assert updated.name == "Admin configured Premium"
    assert updated.price == Decimal("25.00")
    assert updated.currency == "USD"
    assert updated.duration_days == 180


def test_purchase_snapshot_is_immutable_when_plan_changes(catalog_session):
    plan = Plan(
        code="premium_once",
        name="Premium once",
        billing_type="one_time",
        duration_type="fixed",
        duration_days=30,
        price=Decimal("9.00"),
        currency="USD",
        is_active=True,
    )
    purchase = PersonalPurchase.pending_from_plan(
        user_id=uuid4(),
        plan=plan,
    )
    activated_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert purchase.duration_type_snapshot == "fixed"
    assert purchase.duration_days_snapshot == 30
    assert purchase.purchased_price == Decimal("9.00")
    assert purchase.purchased_currency == "USD"
    assert purchase.expires_at is None
    assert purchase.expiration_at(activated_at) == datetime(
        2026, 12, 1, tzinfo=timezone.utc
    )

    plan.duration_days = 365
    plan.price = Decimal("19.00")
    assert purchase.duration_days_snapshot == 30
    assert purchase.purchased_price == Decimal("9.00")


def test_lifetime_purchase_snapshot_has_no_expiration():
    plan = Plan(
        code="premium_lifetime",
        name="Premium lifetime",
        billing_type="one_time",
        duration_type="lifetime",
        duration_days=None,
        price=None,
        currency=None,
        is_active=True,
    )
    purchase = PersonalPurchase.pending_from_plan(
        user_id=uuid4(),
        plan=plan,
    )
    assert purchase.duration_type_snapshot == "lifetime"
    assert purchase.duration_days_snapshot is None
    assert purchase.expiration_at(datetime.now(timezone.utc)) is None
