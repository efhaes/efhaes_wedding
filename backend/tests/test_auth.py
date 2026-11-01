from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    get_db,
    require_platform_admin,
    require_workspace_role,
)
from app.core.security import hash_password, hash_refresh_token
from app.models.rate_limit_bucket import RateLimitBucket
from app.main import app
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.users.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


@app.get("/test-only/platform-admin")
def _platform_admin_probe(user: User = Depends(require_platform_admin)) -> dict[str, str]:
    return {"role": user.platform_role}


@app.get("/test-only/workspaces/{workspace_id}")
def _workspace_admin_probe(
    membership: WorkspaceMember = Depends(require_workspace_role("owner", "admin")),
) -> dict[str, str]:
    return {"role": membership.role}


@pytest.fixture
def auth_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        RefreshToken.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        RateLimitBucket.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, test_session
    app.dependency_overrides.clear()
    User.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _csrf(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _register(client: TestClient, email: str = "person@example.com") -> dict:
    headers = _csrf(client)
    response = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={
            "name": "Person",
            "email": email,
            "password": "valid-password-123",
            "password_confirmation": "valid-password-123",
        },
    )
    return response


def _login(client: TestClient, email: str = "person@example.com", password: str = "valid-password-123"):
    headers = _csrf(client)
    return client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": email, "password": password},
    )


def test_register_creates_personal_user_and_rejects_duplicate_and_mismatch(auth_context):
    client, session_factory = auth_context
    response = _register(client)
    assert response.status_code == 202
    assert response.json() == {
        "message": "If the address is eligible, registration will proceed."
    }
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "person@example.com"))
        assert user is not None
        assert user.platform_role == "user"
        assert user.password_hash != "valid-password-123"

    duplicate = _register(client)
    assert duplicate.status_code == 202
    assert duplicate.json() == response.json()

    mismatch = client.post(
        "/api/v1/auth/register",
        headers=_csrf(client),
        json={
            "name": "Other",
            "email": "other@example.com",
            "password": "valid-password-123",
            "password_confirmation": "different-password",
        },
    )
    assert mismatch.status_code == 422
    admin_registration = client.post(
        "/api/v1/auth/register",
        headers=_csrf(client),
        json={
            "name": "Other",
            "email": "other@example.com",
            "password": "valid-password-123",
            "password_confirmation": "valid-password-123",
            "platform_role": "admin",
        },
    )
    assert admin_registration.status_code == 422


def test_login_is_rate_limited_per_client_not_per_account(auth_context, monkeypatch):
    from app.core.config import settings

    client, _ = auth_context
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_REQUESTS", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_WINDOW_SECONDS", 60)

    first = _login(client, "victim@example.com", "wrong-password")
    second = _login(client, "victim@example.com", "wrong-password")
    limited = _login(client, "victim@example.com", "wrong-password")

    assert first.status_code == 401
    assert second.status_code == 401
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_registration_and_refresh_are_rate_limited(auth_context, monkeypatch):
    from app.core.config import settings

    client, _ = auth_context
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER_REQUESTS", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_REFRESH_REQUESTS", 1)

    assert _register(client).status_code == 202
    assert _register(client).status_code == 429
    assert _login(client).status_code == 200
    assert client.post("/api/v1/auth/refresh", headers=_csrf(client)).status_code == 200
    limited_refresh = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert limited_refresh.status_code == 429


def test_login_current_user_and_inactive_account(auth_context):
    client, session_factory = auth_context
    assert client.get("/api/v1/auth/me").status_code == 401
    assert _register(client).status_code == 202

    invalid = _login(client, password="wrong-password")
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Email or password is incorrect"

    logged_in = _login(client)
    assert logged_in.status_code == 200
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies
    assert "access_token" not in logged_in.json()
    assert "refresh_token" not in logged_in.json()
    assert client.get("/api/v1/auth/me").json()["email"] == "person@example.com"

    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "person@example.com"))
        user.is_active = False
        session.commit()
    inactive = _login(client)
    assert inactive.status_code == 401
    assert inactive.json()["detail"] == "Email or password is incorrect"


def test_refresh_rotates_tokens_and_reuse_revokes_active_sessions(auth_context):
    client, session_factory = auth_context
    assert _register(client).status_code == 202
    assert _login(client).status_code == 200
    old_token = client.cookies["refresh_token"]

    refreshed = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert refreshed.status_code == 200
    rotated_token = client.cookies["refresh_token"]
    assert rotated_token != old_token
    with session_factory() as session:
        old_record = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(old_token))
        )
        new_record = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(rotated_token))
        )
        assert old_record.revoked_reason == "rotated"
        assert new_record.revoked_at is None

    client.cookies.delete("refresh_token", path="/api/v1/auth")
    client.cookies.set("refresh_token", old_token, path="/api/v1/auth")
    replay = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert replay.status_code == 401
    with session_factory() as session:
        active_count = session.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
        )
        assert active_count == 0


def test_expired_refresh_token_is_revoked(auth_context):
    client, session_factory = auth_context
    assert _register(client).status_code == 202
    assert _login(client).status_code == 200
    token = client.cookies["refresh_token"]
    with session_factory() as session:
        record = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
        )
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    response = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert response.status_code == 401
    with session_factory() as session:
        record = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
        )
        assert record.revoked_reason == "expired"


def test_logout_revokes_only_current_device(auth_context):
    client_a, session_factory = auth_context
    assert _register(client_a).status_code == 202
    client_b = TestClient(app)
    assert _login(client_a).status_code == 200
    assert _login(client_b).status_code == 200
    token_a = client_a.cookies["refresh_token"]
    token_b = client_b.cookies["refresh_token"]

    logged_out = client_a.post("/api/v1/auth/logout", headers=_csrf(client_a))
    assert logged_out.status_code == 204
    assert "access_token" not in client_a.cookies
    assert client_b.get("/api/v1/auth/me").status_code == 200
    with session_factory() as session:
        record_a = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token_a))
        )
        record_b = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token_b))
        )
        assert record_a.revoked_reason == "logout"
        assert record_b.revoked_at is None


def test_logout_with_invalid_refresh_token_remains_safe(auth_context):
    client, _ = auth_context
    headers = _csrf(client)
    client.cookies.set("refresh_token", "invalid-token", path="/api/v1/auth")
    response = client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 204


def test_password_change_invalidates_access_and_refresh_sessions(auth_context):
    client, session_factory = auth_context
    assert _register(client).status_code == 202
    assert _login(client).status_code == 200
    old_refresh = client.cookies["refresh_token"]

    wrong_current_password = client.patch(
        "/api/v1/users/me/password",
        headers=_csrf(client),
        json={
            "current_password": "incorrect-password",
            "new_password": "new-valid-password-456",
            "password_confirmation": "new-valid-password-456",
        },
    )
    assert wrong_current_password.status_code == 400

    response = client.patch(
        "/api/v1/users/me/password",
        headers=_csrf(client),
        json={
            "current_password": "valid-password-123",
            "new_password": "new-valid-password-456",
            "password_confirmation": "new-valid-password-456",
        },
    )
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/refresh", headers=_csrf(client)).status_code == 401
    assert _login(client, password="new-valid-password-456").status_code == 200
    with session_factory() as session:
        record = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(old_refresh))
        )
        assert record.revoked_reason == "password_changed"


def test_csrf_and_profile_update(auth_context):
    client, _ = auth_context
    assert _register(client).status_code == 202
    assert _login(client).status_code == 200
    no_csrf = client.patch("/api/v1/users/me", json={"name": "Changed"})
    assert no_csrf.status_code == 403

    updated = client.patch(
        "/api/v1/users/me",
        headers=_csrf(client),
        json={"name": "  Updated Name  "},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Name"
    assert "password_hash" not in updated.json()


def test_platform_and_workspace_authorization(auth_context):
    client, session_factory = auth_context
    assert _register(client).status_code == 202
    assert _login(client).status_code == 200
    assert client.get("/test-only/platform-admin").status_code == 403

    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "person@example.com"))
        workspace = Workspace(name="Studio", slug="studio", owner_id=user.id)
        session.add(workspace)
        session.flush()
        membership = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="editor",
            status="active",
        )
        session.add(membership)
        user.platform_role = "admin"
        session.commit()
        workspace_id = workspace.id

    assert client.get("/test-only/platform-admin").json() == {"role": "admin"}
    assert client.get(f"/test-only/workspaces/{workspace_id}").status_code == 403
    with session_factory() as session:
        membership = session.scalar(select(WorkspaceMember))
        membership.role = "admin"
        session.commit()
    assert client.get(f"/test-only/workspaces/{workspace_id}").json() == {"role": "admin"}
