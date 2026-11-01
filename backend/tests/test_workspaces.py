from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.core.dependencies import get_db
from app.core.security import hash_password
from app.main import app
from app.models import Client, Workspace, WorkspaceMember
from app.models import RateLimitBucket
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.users.models.user import User


@pytest.fixture
def workspace_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    tables = [
        User.__table__,
        RefreshToken.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Client.__table__,
        RateLimitBucket.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        owner = User(
            name="Workspace Owner",
            email="owner@example.com",
            password_hash=hash_password("owner-password-123"),
        )
        outsider = User(
            name="Outsider",
            email="outsider@example.com",
            password_hash=hash_password("outsider-password-123"),
        )
        admin = User(
            name="Workspace Admin",
            email="admin@example.com",
            password_hash=hash_password("admin-password-123"),
        )
        editor = User(
            name="Workspace Editor",
            email="editor@example.com",
            password_hash=hash_password("editor-password-123"),
        )
        session.add_all([owner, outsider, admin, editor])
        session.commit()
        owner_id = owner.id
        admin_id = admin.id
        editor_id = editor.id

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, test_session, owner_id, admin_id, editor_id
    app.dependency_overrides.clear()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        User.metadata.drop_all(connection, tables=tables)
    engine.dispose()


def _csrf(client: TestClient) -> dict[str, str]:
    client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers=_csrf(client),
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def _create_workspace(client: TestClient, name: str = "Wedding Studio") -> dict:
    return client.post(
        "/api/v1/workspaces/",
        headers=_csrf(client),
        json={"name": name},
    )


def test_workspace_creation_owner_membership_listing_update_and_deactivation(workspace_context):
    client, session_factory, owner_id, _, _ = workspace_context
    _login(client, "owner@example.com", "owner-password-123")

    created = _create_workspace(client)
    assert created.status_code == 201
    workspace = created.json()
    workspace_id = UUID(workspace["id"])
    assert workspace["owner_id"] == str(owner_id)

    with session_factory() as session:
        membership = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == owner_id,
            )
        )
        assert membership is not None
        assert membership.role == "owner"
        assert membership.status == "active"

    listed = client.get("/api/v1/workspaces/")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [workspace["id"]]
    detail = client.get(f"/api/v1/workspaces/{workspace_id}")
    assert detail.status_code == 200

    updated = client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        headers=_csrf(client),
        json={"name": "Updated Studio"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Studio"

    deactivated = client.delete(
        f"/api/v1/workspaces/{workspace_id}",
        headers=_csrf(client),
    )
    assert deactivated.status_code == 204
    assert client.get(f"/api/v1/workspaces/{workspace_id}").status_code == 403
    assert client.get("/api/v1/workspaces/").json() == []
    with session_factory() as session:
        stored = session.get(Workspace, workspace_id)
        assert stored.status == "archived"


def test_member_management_roles_and_owner_protection(workspace_context):
    client, session_factory, owner_id, admin_id, editor_id = workspace_context
    _login(client, "owner@example.com", "owner-password-123")
    workspace_id = UUID(_create_workspace(client).json()["id"])

    owner_member = client.get(f"/api/v1/workspaces/{workspace_id}/members").json()[0]
    add_admin = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(client),
        json={"email": "admin@example.com", "role": "admin"},
    )
    assert add_admin.status_code == 201
    admin_member = add_admin.json()
    assert admin_member["user_id"] == str(admin_id)

    add_editor = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(client),
        json={"email": "editor@example.com", "role": "editor"},
    )
    assert add_editor.status_code == 201
    editor_member = add_editor.json()

    duplicate = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(client),
        json={"email": "editor@example.com", "role": "member"},
    )
    assert duplicate.status_code == 409
    assert client.get(f"/api/v1/workspaces/{workspace_id}/members").status_code == 200
    assert client.get(
        f"/api/v1/workspaces/{workspace_id}/members/{editor_member['id']}"
    ).status_code == 200

    changed = client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{editor_member['id']}",
        headers=_csrf(client),
        json={"role": "member"},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "member"

    owner_change = client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner_member['id']}",
        headers=_csrf(client),
        json={"role": "member"},
    )
    assert owner_change.status_code == 409
    owner_remove = client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{owner_member['id']}",
        headers=_csrf(client),
    )
    assert owner_remove.status_code == 409

    admin_client = TestClient(app)
    _login(admin_client, "admin@example.com", "admin-password-123")
    admin_edit_owner = admin_client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner_member['id']}",
        headers=_csrf(admin_client),
        json={"role": "member"},
    )
    assert admin_edit_owner.status_code == 409
    admin_edit_admin = admin_client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{admin_member['id']}",
        headers=_csrf(admin_client),
        json={"role": "editor"},
    )
    assert admin_edit_admin.status_code == 403
    admin_add_admin = admin_client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(admin_client),
        json={"email": "outsider@example.com", "role": "admin"},
    )
    assert admin_add_admin.status_code == 403

    removed = client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{editor_member['id']}",
        headers=_csrf(client),
    )
    assert removed.status_code == 204
    assert client.get(
        f"/api/v1/workspaces/{workspace_id}/members/{editor_member['id']}"
    ).status_code == 404
    with session_factory() as session:
        owner = session.scalar(
            select(WorkspaceMember).where(WorkspaceMember.user_id == owner_id)
        )
        editor = session.scalar(
            select(WorkspaceMember).where(WorkspaceMember.user_id == editor_id)
        )
        assert owner.role == "owner"
        assert editor.status == "suspended"


def test_nonmember_and_unauthenticated_access_is_denied(workspace_context):
    client, _, _, _, _ = workspace_context
    unauthenticated = client.post(
        "/api/v1/workspaces/",
        headers=_csrf(client),
        json={"name": "Private Studio"},
    )
    assert unauthenticated.status_code == 401

    _login(client, "owner@example.com", "owner-password-123")
    workspace_id = UUID(_create_workspace(client).json()["id"])

    outsider = TestClient(app)
    _login(outsider, "outsider@example.com", "outsider-password-123")
    assert outsider.get(f"/api/v1/workspaces/{workspace_id}").status_code == 403
    assert outsider.get(f"/api/v1/workspaces/{workspace_id}/members").status_code == 403
    assert outsider.patch(
        f"/api/v1/workspaces/{workspace_id}",
        headers=_csrf(outsider),
        json={"name": "Stolen"},
    ).status_code == 403


def test_member_add_rejects_unknown_user_and_invalid_request_fields(workspace_context):
    client, _, _, _, _ = workspace_context
    _login(client, "owner@example.com", "owner-password-123")
    workspace_id = UUID(_create_workspace(client).json()["id"])
    unknown = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(client),
        json={"email": "missing@example.com", "role": "member"},
    )
    assert unknown.status_code == 404
    invalid = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=_csrf(client),
        json={"email": "not-an-email", "role": "owner", "user_id": str(uuid4())},
    )
    assert invalid.status_code == 422


def test_workspace_owner_id_requires_matching_membership(workspace_context):
    _, session_factory, owner_id, admin_id, _ = workspace_context
    with session_factory() as session:
        workspace = Workspace(
            name="Invalid owner relation",
            slug="invalid-owner-relation",
            owner_id=owner_id,
            status="active",
        )
        session.add(workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=admin_id,
                role="owner",
                status="active",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
