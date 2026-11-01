from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.security import hash_password
from app.main import app
from app.models.client import Client
from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.models.invitation_section import InvitationSection
from app.models.personal_purchase import PersonalPurchase
from app.models.rate_limit_bucket import RateLimitBucket
from app.models.plan import Plan
from app.models.plan_feature import PlanFeature
from app.models.subscription import Subscription
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.invitations.models.invitation import Invitation
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def invitation_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        RefreshToken.__table__,
        Feature.__table__,
        Plan.__table__,
        PlanFeature.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Client.__table__,
        Template.__table__,
        Invitation.__table__,
        InvitationSection.__table__,
        PersonalPurchase.__table__,
        Subscription.__table__,
        EntitlementOverride.__table__,
        RateLimitBucket.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        users = [
            User(name=name, email=email, password_hash=hash_password("password-12345"))
            for name, email in (
                ("Owner", "owner@example.com"),
                ("Admin", "admin@example.com"),
                ("Editor", "editor@example.com"),
                ("Member", "member@example.com"),
                ("Outsider", "outsider@example.com"),
                ("Other Owner", "other@example.com"),
            )
        ]
        session.add_all(users)
        template = Template(
            name="Published Template",
            slug="published-template",
            category="classic",
            access_level="free",
            configuration={},
            status="published",
            is_active=True,
        )
        draft = Template(
            name="Draft Template",
            slug="draft-template",
            category="classic",
            access_level="free",
            configuration={},
            status="draft",
            is_active=True,
        )
        archived = Template(
            name="Archived Template",
            slug="archived-template",
            category="classic",
            access_level="free",
            configuration={},
            status="archived",
            is_active=False,
        )
        premium = Template(
            name="Premium Template",
            slug="premium-template",
            category="classic",
            access_level="premium",
            configuration={},
            status="published",
            is_active=True,
        )
        inactive_premium = Template(
            name="Inactive Premium Template",
            slug="inactive-premium-template",
            category="classic",
            access_level="premium",
            configuration={},
            status="published",
            is_active=False,
        )
        feature = Feature(
            code="premium_templates",
            name="Premium templates",
            feature_type="boolean",
        )
        personal_plan = Plan(
            code="personal-premium",
            name="Personal Premium",
            billing_type="one_time",
            price=9.99,
            currency="USD",
            duration_days=365,
        )
        workspace_plan = Plan(
            code="workspace-premium",
            name="Workspace Premium",
            billing_type="subscription",
            price=19.99,
            currency="USD",
        )
        session.add_all(
            [
                template,
                draft,
                archived,
                premium,
                inactive_premium,
                feature,
                personal_plan,
                workspace_plan,
            ]
        )
        session.flush()
        session.add_all(
            [
                PlanFeature(
                    plan_id=personal_plan.id,
                    feature_id=feature.id,
                    configuration={},
                ),
                PlanFeature(
                    plan_id=workspace_plan.id,
                    feature_id=feature.id,
                    configuration={},
                ),
            ]
        )
        workspace_one = Workspace(
            name="First Studio",
            slug="first-studio",
            owner_id=users[0].id,
            status="active",
        )
        workspace_two = Workspace(
            name="Other Studio",
            slug="other-studio",
            owner_id=users[5].id,
            status="active",
        )
        session.add_all([workspace_one, workspace_two])
        session.flush()
        memberships = [
            WorkspaceMember(
                workspace_id=workspace_one.id,
                user_id=users[0].id,
                role="owner",
                status="active",
            ),
            WorkspaceMember(
                workspace_id=workspace_one.id,
                user_id=users[1].id,
                role="admin",
                status="active",
            ),
            WorkspaceMember(
                workspace_id=workspace_one.id,
                user_id=users[2].id,
                role="editor",
                status="active",
            ),
            WorkspaceMember(
                workspace_id=workspace_one.id,
                user_id=users[3].id,
                role="member",
                status="active",
            ),
            WorkspaceMember(
                workspace_id=workspace_two.id,
                user_id=users[5].id,
                role="owner",
                status="active",
            ),
        ]
        session.add_all(memberships)
        session.flush()
        client_one = Client(workspace_id=workspace_one.id, name="Client One")
        client_two = Client(workspace_id=workspace_two.id, name="Client Two")
        session.add_all([client_one, client_two])
        session.commit()
        ids = {
            "users": [user.id for user in users],
            "template": template.id,
            "premium_template": premium.id,
            "inactive_premium_template": inactive_premium.id,
            "draft_template": draft.id,
            "archived_template": archived.id,
            "premium_feature": feature.id,
            "personal_plan": personal_plan.id,
            "workspace_plan": workspace_plan.id,
            "workspace_one": workspace_one.id,
            "workspace_two": workspace_two.id,
            "client_one": client_one.id,
            "client_two": client_two.id,
        }

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, test_session, ids
    app.dependency_overrides.clear()
    User.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _csrf(client: TestClient) -> dict[str, str]:
    client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers=_csrf(client),
        json={"email": email, "password": "password-12345"},
    )
    assert response.status_code == 200


def _personal_payload(template_id: UUID, **changes) -> dict:
    return {"title": "Wedding Project", "template_id": str(template_id), **changes}


def _create_personal(client: TestClient, template_id: UUID, **changes):
    return client.post(
        "/api/v1/invitations/personal",
        headers=_csrf(client),
        json=_personal_payload(template_id, **changes),
    )


def _create_workspace(client: TestClient, workspace_id: UUID, template_id: UUID, **changes):
    payload = _personal_payload(template_id, **changes)
    return client.post(
        f"/api/v1/invitations/workspaces/{workspace_id}",
        headers=_csrf(client),
        json=payload,
    )


def test_personal_invitation_crud_and_owner_isolation(invitation_context):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")
    created = _create_personal(client, ids["template"])
    assert created.status_code == 201
    invitation = created.json()
    invitation_id = UUID(invitation["id"])
    assert invitation["owner_id"] == str(ids["users"][0])
    assert invitation["workspace_id"] is None
    assert invitation["status"] == "draft"

    with session_factory() as session:
        stored = session.get(Invitation, invitation_id)
        assert stored.owner_id == ids["users"][0]
        assert stored.workspace_id is None

    listing = client.get("/api/v1/invitations/personal")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    detail = client.get(f"/api/v1/invitations/personal/{invitation_id}")
    assert detail.status_code == 200

    updated = client.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(client),
        json={"title": "Updated Wedding Project"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated Wedding Project"

    owner_b = TestClient(app)
    _login(owner_b, "other@example.com")
    assert owner_b.get(f"/api/v1/invitations/personal/{invitation_id}").status_code == 404
    assert owner_b.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(owner_b),
        json={"title": "Unauthorized"},
    ).status_code == 404

    archived = client.post(
        f"/api/v1/invitations/personal/{invitation_id}/archive",
        headers=_csrf(client),
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert client.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(client),
        json={"title": "No longer editable"},
    ).status_code == 409


def test_workspace_creation_permissions_by_member_role(invitation_context):
    client, session_factory, ids = invitation_context
    workspace_id = ids["workspace_one"]

    _login(client, "admin@example.com")
    admin_created = _create_workspace(client, workspace_id, ids["template"])
    assert admin_created.status_code == 201
    assert admin_created.json()["owner_id"] is None
    assert admin_created.json()["workspace_id"] == str(workspace_id)

    editor = TestClient(app)
    _login(editor, "editor@example.com")
    editor_created = _create_workspace(editor, workspace_id, ids["template"])
    assert editor_created.status_code == 201
    editor_invitation_id = UUID(editor_created.json()["id"])

    changed = editor.patch(
        f"/api/v1/invitations/workspaces/{workspace_id}/{editor_invitation_id}",
        headers=_csrf(editor),
        json={"title": "Editor Change"},
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "Editor Change"

    assert editor.post(
        f"/api/v1/invitations/workspaces/{workspace_id}/{editor_invitation_id}/archive",
        headers=_csrf(editor),
    ).status_code == 403

    member = TestClient(app)
    _login(member, "member@example.com")
    member_listing = member.get(f"/api/v1/invitations/workspaces/{workspace_id}")
    assert member_listing.status_code == 200
    member_create = _create_workspace(member, workspace_id, ids["template"])
    assert member_create.status_code == 403
    assert member.patch(
        f"/api/v1/invitations/workspaces/{workspace_id}/{editor_invitation_id}",
        headers=_csrf(member),
        json={"title": "Member Change"},
    ).status_code == 403

    with session_factory() as session:
        stored = session.get(Invitation, editor_invitation_id)
        assert stored.workspace_id == workspace_id
        assert stored.owner_id is None


def test_workspace_access_is_membership_scoped_and_archived_workspace_denied(invitation_context):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")
    workspace_id = ids["workspace_one"]
    created = _create_workspace(client, workspace_id, ids["template"])
    invitation_id = UUID(created.json()["id"])

    outsider = TestClient(app)
    _login(outsider, "outsider@example.com")
    assert outsider.get(
        f"/api/v1/invitations/workspaces/{workspace_id}"
    ).status_code == 403
    assert outsider.get(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}"
    ).status_code == 403
    other_workspace = TestClient(app)
    _login(other_workspace, "other@example.com")
    assert other_workspace.get(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}"
    ).status_code == 403
    with session_factory() as session:
        platform_user = session.scalar(
            select(User).where(User.email == "outsider@example.com")
        )
        platform_user.platform_role = "admin"
        session.commit()
    assert outsider.get(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}"
    ).status_code == 403

    with session_factory() as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.status = "archived"
        session.commit()
    assert _create_workspace(client, workspace_id, ids["template"]).status_code == 403
    assert client.get(
        f"/api/v1/invitations/workspaces/{workspace_id}"
    ).status_code == 403


def test_workspace_archive_admin_allowed_and_member_denied(invitation_context):
    client, _, ids = invitation_context
    workspace_id = ids["workspace_one"]
    _login(client, "owner@example.com")
    created = _create_workspace(client, workspace_id, ids["template"])
    invitation_id = UUID(created.json()["id"])

    member = TestClient(app)
    _login(member, "member@example.com")
    assert member.post(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/archive",
        headers=_csrf(member),
    ).status_code == 403

    admin = TestClient(app)
    _login(admin, "admin@example.com")
    response = admin.post(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/archive",
        headers=_csrf(admin),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"


def test_template_availability_and_client_workspace_validation(invitation_context):
    client, _, ids = invitation_context
    workspace_id = ids["workspace_one"]
    _login(client, "owner@example.com")

    valid_client = _create_workspace(
        client,
        workspace_id,
        ids["template"],
        client_id=str(ids["client_one"]),
    )
    assert valid_client.status_code == 201
    assert valid_client.json()["client_id"] == str(ids["client_one"])

    foreign_client = _create_workspace(
        client,
        workspace_id,
        ids["template"],
        client_id=str(ids["client_two"]),
    )
    assert foreign_client.status_code == 422

    assert _create_personal(
        client,
        ids["template"],
        client_id=str(ids["client_one"]),
    ).status_code == 422
    for template_key in ("draft_template", "archived_template", "missing_template"):
        template_id = ids.get(template_key, UUID("00000000-0000-0000-0000-000000000001"))
        assert _create_personal(client, template_id).status_code == 422
    invitation_id = UUID(valid_client.json()["id"])
    assert client.patch(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}",
        headers=_csrf(client),
        json={"client_id": str(ids["client_two"])},
    ).status_code == 422
    cleared = client.patch(
        f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}",
        headers=_csrf(client),
        json={"client_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["client_id"] is None


def test_premium_template_requires_personal_invitation_purchase(
    invitation_context,
):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")

    free = _create_personal(client, ids["template"])
    assert free.status_code == 201
    assert _create_personal(client, ids["premium_template"]).status_code == 403

    invitation_id = UUID(free.json()["id"])
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            PersonalPurchase(
                user_id=ids["users"][0],
                invitation_id=invitation_id,
                plan_id=ids["personal_plan"],
                status="active",
                activated_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
            )
        )
        session.commit()

    upgraded = client.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(client),
        json={"template_id": str(ids["premium_template"])},
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["template_id"] == str(ids["premium_template"])


@pytest.mark.parametrize(
    ("purchase_status", "activated_offset", "expires_offset"),
    [
        ("active", -2, -1),
        ("expired", -10, 10),
        ("cancelled", -2, 10),
        ("pending", -2, 10),
    ],
)
def test_personal_purchase_must_be_active_and_unexpired(
    invitation_context,
    purchase_status,
    activated_offset,
    expires_offset,
):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")
    created = _create_personal(client, ids["template"])
    assert created.status_code == 201
    invitation_id = UUID(created.json()["id"])
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            PersonalPurchase(
                user_id=ids["users"][0],
                invitation_id=invitation_id,
                plan_id=ids["personal_plan"],
                status=purchase_status,
                activated_at=now + timedelta(days=activated_offset),
                expires_at=now + timedelta(days=expires_offset),
            )
        )
        session.commit()

    denied = client.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(client),
        json={"template_id": str(ids["premium_template"])},
    )
    assert denied.status_code == 403
    title_update = client.patch(
        f"/api/v1/invitations/personal/{invitation_id}",
        headers=_csrf(client),
        json={"title": "Still editable"},
    )
    assert title_update.status_code == 200


def test_workspace_template_access_requires_current_matching_subscription(
    invitation_context,
):
    client, session_factory, ids = invitation_context
    workspace_id = ids["workspace_one"]
    _login(client, "owner@example.com")

    assert _create_workspace(client, workspace_id, ids["template"]).status_code == 201
    assert _create_workspace(
        client,
        workspace_id,
        ids["premium_template"],
    ).status_code == 403

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            Subscription(
                workspace_id=workspace_id,
                plan_id=ids["workspace_plan"],
                status="active",
                current_period_start=now - timedelta(days=2),
                current_period_end=now + timedelta(days=30),
                cancel_at_period_end=False,
            )
        )
        session.commit()

    premium = _create_workspace(
        client,
        workspace_id,
        ids["premium_template"],
    )
    assert premium.status_code == 201, premium.text
    assert premium.json()["workspace_id"] == str(workspace_id)


@pytest.mark.parametrize("subscription_status", ["expired", "cancelled", "past_due"])
def test_expired_cancelled_or_past_due_subscription_denies_premium(
    invitation_context,
    subscription_status,
):
    client, session_factory, ids = invitation_context
    workspace_id = ids["workspace_one"]
    _login(client, "owner@example.com")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            Subscription(
                workspace_id=workspace_id,
                plan_id=ids["workspace_plan"],
                status=subscription_status,
                current_period_start=now - timedelta(days=10),
                current_period_end=now - timedelta(days=1),
                cancel_at_period_end=False,
            )
        )
        session.commit()

    denied = _create_workspace(
        client,
        workspace_id,
        ids["premium_template"],
    )
    assert denied.status_code == 403


def test_personal_and_workspace_entitlements_do_not_cross_scopes(
    invitation_context,
):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")
    personal = _create_personal(client, ids["template"])
    assert personal.status_code == 201
    personal_id = UUID(personal.json()["id"])
    another_personal = _create_personal(client, ids["template"])
    assert another_personal.status_code == 201
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            PersonalPurchase(
                user_id=ids["users"][0],
                invitation_id=personal_id,
                plan_id=ids["personal_plan"],
                status="active",
                activated_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
            )
        )
        session.commit()
        session.add(
            Subscription(
                workspace_id=ids["workspace_two"],
                plan_id=ids["workspace_plan"],
                status="active",
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=30),
                cancel_at_period_end=False,
            )
        )
        session.commit()
    assert client.patch(
        f"/api/v1/invitations/personal/{another_personal.json()['id']}",
        headers=_csrf(client),
        json={"template_id": str(ids["premium_template"])},
    ).status_code == 403
    assert _create_workspace(
        client,
        ids["workspace_one"],
        ids["premium_template"],
    ).status_code == 403
    assert _create_personal(
        client,
        ids["premium_template"],
    ).status_code == 403


def test_entitlement_overrides_apply_to_their_exact_scope(invitation_context):
    client, session_factory, ids = invitation_context
    _login(client, "owner@example.com")
    personal = _create_personal(client, ids["template"])
    assert personal.status_code == 201
    personal_id = UUID(personal.json()["id"])
    with session_factory() as session:
        session.add_all(
            [
                EntitlementOverride(
                    invitation_id=personal_id,
                    feature_id=ids["premium_feature"],
                    is_enabled=True,
                    reason="approved invitation exception",
                ),
                EntitlementOverride(
                    workspace_id=ids["workspace_one"],
                    feature_id=ids["premium_feature"],
                    is_enabled=True,
                    reason="approved workspace exception",
                ),
            ]
        )
        session.commit()

    personal_upgrade = client.patch(
        f"/api/v1/invitations/personal/{personal_id}",
        headers=_csrf(client),
        json={"template_id": str(ids["premium_template"])},
    )
    assert personal_upgrade.status_code == 200, personal_upgrade.text
    workspace_create = _create_workspace(
        client,
        ids["workspace_one"],
        ids["premium_template"],
    )
    assert workspace_create.status_code == 201, workspace_create.text


def test_premium_template_requires_published_and_active_state(
    invitation_context,
):
    client, _, ids = invitation_context
    _login(client, "owner@example.com")
    assert _create_personal(
        client,
        ids["inactive_premium_template"],
    ).status_code == 422


def test_invitation_lists_search_pagination_filter_and_forged_ownership_rejected(
    invitation_context,
):
    client, _, ids = invitation_context
    _login(client, "owner@example.com")
    first = _create_personal(client, ids["template"], title="First Wedding")
    second = _create_personal(client, ids["template"], title="Second Wedding")
    assert first.status_code == second.status_code == 201

    search = client.get("/api/v1/invitations/personal?search=First")
    assert search.json()["total"] == 1
    page = client.get("/api/v1/invitations/personal?page=2&page_size=1")
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    archived = client.post(
        f"/api/v1/invitations/personal/{first.json()['id']}/archive",
        headers=_csrf(client),
    )
    assert archived.status_code == 200
    filtered = client.get("/api/v1/invitations/personal?status=archived")
    assert filtered.json()["total"] == 1

    forged = client.post(
        "/api/v1/invitations/personal",
        headers=_csrf(client),
        json=_personal_payload(
            ids["template"],
            owner_id=str(ids["users"][4]),
            workspace_id=str(ids["workspace_two"]),
        ),
    )
    assert forged.status_code == 422
    workspace_forged = client.post(
        f"/api/v1/invitations/workspaces/{ids['workspace_one']}",
        headers=_csrf(client),
        json=_personal_payload(
            ids["template"],
            owner_id=str(ids["users"][0]),
            workspace_id=str(ids["workspace_two"]),
        ),
    )
    assert workspace_forged.status_code == 422


def test_unauthenticated_user_cannot_create_or_list_personal_invitations(invitation_context):
    client, _, ids = invitation_context
    assert client.get("/api/v1/invitations/personal").status_code == 401
    assert _create_personal(client, ids["template"]).status_code == 401
