from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, get_db
from app.core.security import hash_password
from app.database.base import Base
from app.main import app
from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.personal_purchase import PersonalPurchase
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def catalog_admin_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        Plan.__table__,
        Feature.__table__,
        PlanFeature.__table__,
        PersonalPurchase.__table__,
        Subscription.__table__,
        EntitlementOverride.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    identity: dict[str, User | None] = {"user": None}

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    def override_current_user():
        return identity["user"]

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    with TestClient(app) as client:
        yield client, test_session, identity
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _set_identity(identity: dict[str, User | None], role: str | None) -> None:
    if role is None:
        identity["user"] = None
        return
    identity["user"] = User(
        id=uuid4(),
        name=f"{role} user",
        email=f"{role}-{uuid4().hex}@example.invalid",
        password_hash=hash_password("catalog-admin-test-password"),
        platform_role=role,
    )


def _csrf(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", "catalog-test-csrf")
    return {"X-CSRF-Token": "catalog-test-csrf"}


def _plan_payload(**overrides) -> dict:
    payload = {
        "code": f"test_plan_{uuid4().hex[:8]}",
        "name": "Test plan",
        "billing_type": "one_time",
        "duration_type": "fixed",
        "duration_days": 30,
        "price": "10.00",
        "currency": "USD",
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def _feature_payload(**overrides) -> dict:
    payload = {
        "code": f"test_feature_{uuid4().hex[:8]}",
        "name": "Test feature",
        "feature_type": "boolean",
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def test_catalog_admin_requires_platform_admin_and_csrf(catalog_admin_context):
    client, session_factory, identity = catalog_admin_context

    response = client.get("/api/v1/admin/plans")
    assert response.status_code == 401

    _set_identity(identity, "user")
    assert client.get("/api/v1/admin/plans").status_code == 403
    workspace_owner = User(
        id=uuid4(),
        name="Workspace owner",
        email=f"workspace-owner-{uuid4().hex}@example.invalid",
        password_hash=hash_password("catalog-admin-test-password"),
        platform_role="user",
    )
    with session_factory() as session:
        session.add(workspace_owner)
        workspace_id = uuid4()
        session.add_all(
            [
                Workspace(
                    id=workspace_id,
                    name="Owner workspace",
                    slug=f"owner-workspace-{uuid4().hex}",
                    owner_id=workspace_owner.id,
                    status="active",
                ),
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=workspace_owner.id,
                    role="owner",
                    status="active",
                ),
            ]
        )
        session.commit()
    identity["user"] = workspace_owner
    assert client.get("/api/v1/admin/plans").status_code == 403
    assert client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(),
        headers=_csrf(client),
    ).status_code == 403

    _set_identity(identity, "admin")
    assert client.get("/api/v1/admin/plans").status_code == 200
    assert client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(),
    ).status_code == 403


def test_plan_create_update_filter_and_purchase_snapshot(catalog_admin_context):
    client, session_factory, identity = catalog_admin_context
    _set_identity(identity, "admin")
    headers = _csrf(client)

    created = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    plan_id = UUID(plan["id"])
    assert plan["features"] == []
    assert plan["duration_days"] == 30

    duplicate = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(code=plan["code"]),
        headers=headers,
    )
    assert duplicate.status_code == 409

    invalid_fixed = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(code=f"invalid_{uuid4().hex[:8]}", duration_days=None),
        headers=headers,
    )
    assert invalid_fixed.status_code == 422
    active_pending = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(
            code=f"pending_{uuid4().hex[:8]}",
            duration_type="pending",
            duration_days=None,
            price=None,
            currency=None,
        ),
        headers=headers,
    )
    assert active_pending.status_code == 422

    with session_factory() as session:
        stored_plan = session.get(Plan, plan_id)
        purchase = PersonalPurchase(
            user_id=uuid4(),
            plan_id=stored_plan.id,
            status="active",
            duration_type_snapshot="fixed",
            duration_days_snapshot=30,
            purchased_price=Decimal("10.00"),
            purchased_currency="USD",
        )
        session.add(purchase)
        session.commit()
        purchase_id = purchase.id

    updated = client.patch(
        f"/api/v1/admin/plans/{plan['id']}",
        json={"price": "25.00", "duration_days": 90},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["price"] == "25.00"
    assert updated.json()["duration_days"] == 90
    with session_factory() as session:
        purchase = session.get(PersonalPurchase, purchase_id)
        assert purchase.duration_days_snapshot == 30
        assert purchase.purchased_price == Decimal("10.00")

    deactivated = client.patch(
        f"/api/v1/admin/plans/{plan['id']}",
        json={"is_active": False},
        headers=headers,
    )
    assert deactivated.status_code == 200
    with session_factory() as session:
        stored_plan = session.get(Plan, plan_id)
        with pytest.raises(ValueError, match="active one-time plan"):
            PersonalPurchase.pending_from_plan(uuid4(), stored_plan)

    filtered = client.get(
        "/api/v1/admin/plans",
        params={"search": plan["code"], "is_active": "false", "sort_by": "name"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["code"] == plan["code"]
    assert client.delete(
        f"/api/v1/admin/plans/{plan['id']}", headers=headers
    ).status_code == 405


def test_feature_crud_and_deactivation_guard(catalog_admin_context):
    client, session_factory, identity = catalog_admin_context
    _set_identity(identity, "admin")
    headers = _csrf(client)

    created = client.post(
        "/api/v1/admin/features",
        json=_feature_payload(),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    feature = created.json()
    feature_id = UUID(feature["id"])
    assert feature["created_at"] is not None
    assert feature["updated_at"] is not None
    assert feature["plans"] == []

    duplicate = client.post(
        "/api/v1/admin/features",
        json=_feature_payload(code=feature["code"]),
        headers=headers,
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/api/v1/admin/features/{feature['id']}",
        json={"name": "Renamed feature", "description": "Updated"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed feature"

    disabled = client.patch(
        f"/api/v1/admin/features/{feature['id']}",
        json={"is_active": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    filtered = client.get(
        "/api/v1/admin/features",
        params={"search": feature["code"], "is_active": "false", "sort_by": "name"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["code"] == feature["code"]

    plan_response = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(is_active=False),
        headers=headers,
    )
    assert plan_response.status_code == 201
    rejected_mapping = client.post(
        f"/api/v1/admin/plans/{plan_response.json()['id']}/features",
        json={"feature_id": feature["id"], "is_enabled": True},
        headers=headers,
    )
    assert rejected_mapping.status_code == 409

    with session_factory() as session:
        feature_model = session.get(Feature, feature_id)
        assert feature_model.updated_at is not None
        assert session.scalar(
            select(Feature.id).where(Feature.code == feature["code"])
        ) == feature_model.id


def test_plan_feature_management_and_transaction_guards(catalog_admin_context):
    client, session_factory, identity = catalog_admin_context
    _set_identity(identity, "admin")
    headers = _csrf(client)
    plan_response = client.post(
        "/api/v1/admin/plans",
        json=_plan_payload(is_active=False),
        headers=headers,
    )
    feature_response = client.post(
        "/api/v1/admin/features",
        json=_feature_payload(),
        headers=headers,
    )
    assert plan_response.status_code == 201
    assert feature_response.status_code == 201
    plan_id = plan_response.json()["id"]
    feature_id = feature_response.json()["id"]
    plan_uuid = UUID(plan_id)
    feature_uuid = UUID(feature_id)

    mapping_response = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={"feature_id": feature_id, "configuration": {}, "is_enabled": False},
        headers=headers,
    )
    assert mapping_response.status_code == 201, mapping_response.text
    duplicate = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={"feature_id": feature_id, "configuration": {}, "is_enabled": False},
        headers=headers,
    )
    assert duplicate.status_code == 409

    enabled = client.patch(
        f"/api/v1/admin/plans/{plan_id}/features/{feature_id}",
        json={"is_enabled": True},
        headers=headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_enabled"] is True
    plan_detail = client.get(f"/api/v1/admin/plans/{plan_id}")
    assert plan_detail.json()["features"][0]["feature"]["code"] == feature_response.json()["code"]
    feature_detail = client.get(f"/api/v1/admin/features/{feature_id}")
    assert feature_detail.json()["plans"][0]["plan"]["id"] == plan_id

    unknown = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={"feature_id": str(uuid4()), "is_enabled": False},
        headers=headers,
    )
    assert unknown.status_code == 404

    limit_feature_response = client.post(
        "/api/v1/admin/features",
        json=_feature_payload(
            code=f"limit_{uuid4().hex[:8]}",
            feature_type="limit",
        ),
        headers=headers,
    )
    invalid_limit = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={
            "feature_id": limit_feature_response.json()["id"],
            "is_enabled": True,
            "configuration": {},
        },
        headers=headers,
    )
    assert invalid_limit.status_code == 422
    valid_limit = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={
            "feature_id": limit_feature_response.json()["id"],
            "is_enabled": True,
            "configuration": {"limit": 0},
        },
        headers=headers,
    )
    assert valid_limit.status_code == 201
    deleted_limit = client.delete(
        f"/api/v1/admin/plans/{plan_id}/features/{limit_feature_response.json()['id']}",
        headers=headers,
    )
    assert deleted_limit.status_code == 204
    restored_limit = client.post(
        f"/api/v1/admin/plans/{plan_id}/features",
        json={
            "feature_id": limit_feature_response.json()["id"],
            "is_enabled": True,
            "configuration": {"limit": 0},
        },
        headers=headers,
    )
    assert restored_limit.status_code == 201

    with session_factory() as session:
        session.add(
            PersonalPurchase(
                user_id=uuid4(),
                plan_id=plan_uuid,
                status="pending",
            )
        )
        session.commit()

    blocked_update = client.patch(
        f"/api/v1/admin/plans/{plan_id}/features/{feature_id}",
        json={"is_enabled": False},
        headers=headers,
    )
    assert blocked_update.status_code == 409
    blocked_delete = client.delete(
        f"/api/v1/admin/plans/{plan_id}/features/{feature_id}",
        headers=headers,
    )
    assert blocked_delete.status_code == 409
    blocked_feature_deactivation = client.patch(
        f"/api/v1/admin/features/{feature_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert blocked_feature_deactivation.status_code == 409
    with session_factory() as session:
        mapping = session.scalar(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan_uuid,
                PlanFeature.feature_id == feature_uuid,
            )
        )
        assert mapping is not None and mapping.is_enabled is True
