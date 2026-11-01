from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.security import hash_password
from app.main import app
from app.models.client import Client
from app.models.guest import Guest
from app.models.guestbook import GuestbookEntry
from app.models.invitation_section import InvitationSection
from app.models.rate_limit_bucket import RateLimitBucket
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.invitations.models.invitation import Invitation
from app.modules.public_invitations.services.public_invitation_service import (
    guest_token_hash,
)
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def guestbook_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        User.__table__,
        RefreshToken.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Client.__table__,
        Template.__table__,
        Invitation.__table__,
        InvitationSection.__table__,
        Guest.__table__,
        GuestbookEntry.__table__,
        RateLimitBucket.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        users = {
            name: User(
                name=name.title(),
                email=f"{name}@example.com",
                password_hash=hash_password("test-password-123"),
            )
            for name in ("owner", "admin", "editor", "member", "outsider")
        }
        session.add_all(users.values())
        session.flush()
        workspace = Workspace(
            name="Wedding Studio",
            slug="wedding-studio",
            owner_id=users["owner"].id,
            status="active",
        )
        template = Template(
            name="Classic",
            slug="classic",
            category="classic",
            configuration={"default_sections": []},
            status="published",
            is_active=True,
        )
        session.add_all([workspace, template])
        session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=users[name].id,
                    role=role,
                    status="active",
                )
                for name, role in (
                    ("owner", "owner"),
                    ("admin", "admin"),
                    ("editor", "editor"),
                    ("member", "member"),
                )
            ]
        )
        now = datetime.now(timezone.utc)
        invitations = {
            name: Invitation(
                owner_id=(
                    users["owner"].id
                    if name not in ("workspace",)
                    else None
                ),
                workspace_id=workspace.id if name == "workspace" else None,
                client_id=None,
                template_id=template.id,
                title=f"{name.title()} Invitation",
                slug=f"{name}-invitation",
                status=state,
                first_published_at=(
                    now - timedelta(days=2)
                    if state in ("published", "archived")
                    else None
                ),
                expires_at=now - timedelta(days=1) if name == "expired" else None,
            )
            for name, state in (
                ("personal", "published"),
                ("workspace", "published"),
                ("other", "published"),
                ("unpublished", "draft"),
                ("archived", "archived"),
                ("expired", "published"),
                ("disabled", "published"),
                ("anonymous", "published"),
                ("no-messages", "published"),
            )
        }
        session.add_all(invitations.values())
        session.flush()
        for name, invitation in invitations.items():
            if name == "disabled":
                continue
            content = {}
            if name == "anonymous":
                content["allow_anonymous"] = True
            if name == "no-messages":
                content["allow_guest_message"] = False
            session.add(
                InvitationSection(
                    invitation_id=invitation.id,
                    section_key="guestbook",
                    section_type="guestbook",
                    title="Guestbook",
                    content=content,
                    is_enabled=True,
                    sort_order=0,
                )
            )
        guest_data = (
            ("Alice", "personal", "alice-token-0123456789-abcdefghijk"),
            ("Bob", "other", "bob-token-0123456789-abcdefghijk"),
            ("Studio Guest", "workspace", "studio-token-0123456789-abcdefgh"),
        )
        guests = {}
        for name, invitation_key, token in guest_data:
            guest = Guest(
                invitation_id=invitations[invitation_key].id,
                name=name,
                email=f"{name.lower().replace(' ', '.')}@example.com",
                invitation_token=guest_token_hash(token),
                checkin_status="pending",
            )
            session.add(guest)
            guests[name] = (guest, token)
        session.flush()
        seeded_entries = {}
        for status, name, message in (
            ("pending", "Pending Guest", "Awaiting review"),
            ("approved", "Approved Guest", "Welcome"),
            ("hidden", "Hidden Guest", "Private"),
        ):
            entry = GuestbookEntry(
                invitation_id=invitations["personal"].id,
                guest_name=name,
                message=message,
                status=status,
                is_approved=status == "approved",
            )
            session.add(entry)
            seeded_entries[status] = entry
        session.add(
            GuestbookEntry(
                invitation_id=invitations["other"].id,
                guest_name="Other Guest",
                message="Other invitation",
                status="approved",
                is_approved=True,
            )
        )
        session.add(
            GuestbookEntry(
                invitation_id=invitations["workspace"].id,
                guest_name="Workspace Guest",
                message="Looking forward to it",
                status="approved",
                is_approved=True,
            )
        )
        session.commit()
        ids = {
            **{name: user.id for name, user in users.items()},
            "workspace_id": workspace.id,
            **{name: invitation.id for name, invitation in invitations.items()},
            **{name: guest.id for name, (guest, _) in guests.items()},
            **{f"entry_{status}": entry.id for status, entry in seeded_entries.items()},
        }
        tokens = {name: token for name, (_, token) in guests.items()}

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, test_session, ids, tokens
    app.dependency_overrides.clear()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        User.metadata.drop_all(connection, tables=tables)
    engine.dispose()


def _csrf(client: TestClient) -> dict[str, str]:
    client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers=_csrf(client),
        json={"email": email, "password": "test-password-123"},
    )
    assert response.status_code == 200, response.text


def _submit(
    client: TestClient,
    slug: str,
    *,
    token: str | None = None,
    name: str | None = None,
    message: str = "Congratulations!",
    use_authorization: bool = False,
    **extra,
):
    payload = {"message": message, **extra}
    headers = None
    if token is not None and use_authorization:
        headers = {"Authorization": f"Bearer {token}"}
    elif token is not None:
        payload["guest_token"] = token
    if name is not None:
        payload["guest_name"] = name
    return client.post(
        f"/api/v1/public/invitations/{slug}/guestbook",
        headers=headers,
        json=payload,
    )


def _workspace_path(ids, suffix: str = "") -> str:
    return (
        f"/api/v1/invitations/workspaces/{ids['workspace_id']}/"
        f"{ids['workspace']}/guestbook{suffix}"
    )


def test_personalized_submission_is_pending_and_public_payload_is_safe(
    guestbook_context,
):
    client, session_factory, ids, tokens = guestbook_context
    response = _submit(
        client,
        "personal-invitation",
        token=tokens["Alice"],
        message="  Best wishes\r\nand happiness!  ",
        use_authorization=True,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["guest_name"] == "Alice"
    assert body["message"] == "Best wishes\nand happiness!"
    assert "id" not in body
    assert "guest_id" not in body
    assert "invitation_id" not in body
    assert "guest_token" not in body
    assert "moderated_by" not in body
    assert client.get(
        "/api/v1/public/invitations/personal-invitation/guestbook"
    ).json()["total"] == 1

    with session_factory() as session:
        entry = session.query(GuestbookEntry).filter_by(
            invitation_id=ids["personal"],
            guest_id=ids["Alice"],
        ).one()
        assert entry.status == "pending"
        assert not entry.is_approved
        assert entry.guest_name == "Alice"


def test_anonymous_and_auto_approved_submission_follow_section_settings(
    guestbook_context,
):
    client, session_factory, ids, _ = guestbook_context
    blocked_anonymous = _submit(
        client,
        "personal-invitation",
        name="Visitor",
    )
    assert blocked_anonymous.status_code == 403

    anonymous = _submit(
        client,
        "anonymous-invitation",
        name="  Budi    Santoso  ",
        message="  Selamat!  ",
    )
    assert anonymous.status_code == 200
    assert anonymous.json()["guest_name"] == "Budi Santoso"
    assert anonymous.json()["message"] == "Selamat!"
    with session_factory() as session:
        entry = session.query(GuestbookEntry).filter_by(
            invitation_id=ids["anonymous"],
        ).one()
        assert entry.guest_id is None
        assert entry.status == "pending"

        section = session.query(InvitationSection).filter_by(
            invitation_id=ids["anonymous"],
            section_key="guestbook",
        ).one()
        section.content = {
            "allow_anonymous": True,
            "moderation_required": False,
        }
        session.commit()

    approved = _submit(
        client,
        "anonymous-invitation",
        name="Another Visitor",
        message="<script>alert(1)</script>",
    )
    assert approved.status_code == 200
    public = client.get(
        "/api/v1/public/invitations/anonymous-invitation/guestbook"
    )
    assert public.status_code == 200
    assert any(
        item["message"] == "<script>alert(1)</script>"
        for item in public.json()["items"]
    )


def test_public_submission_rejects_bad_tokens_and_unavailable_invitations(
    guestbook_context,
):
    client, _, _, tokens = guestbook_context
    assert _submit(
        client,
        "personal-invitation",
        token="x" * 40,
    ).status_code == 404
    assert _submit(
        client,
        "personal-invitation",
        token=tokens["Bob"],
    ).status_code == 404
    assert _submit(
        client,
        "no-messages-invitation",
        token=tokens["Alice"],
    ).status_code == 409
    assert _submit(
        client,
        "disabled-invitation",
        token=tokens["Alice"],
    ).status_code == 409
    for slug in (
        "unpublished-invitation",
        "archived-invitation",
        "expired-invitation",
        "missing-invitation",
    ):
        assert _submit(client, slug, token=tokens["Alice"]).status_code == 404


def test_public_guestbook_rate_limit_and_body_size(guestbook_context, monkeypatch):
    from app.core.config import settings

    client, _, _, _ = guestbook_context
    monkeypatch.setattr(settings, "RATE_LIMIT_SUBMISSION_REQUESTS", 1)
    first = _submit(client, "anonymous-invitation", name="Visitor")
    limited = _submit(client, "anonymous-invitation", name="Visitor")
    assert first.status_code == 200
    assert limited.status_code == 429

    oversized = client.post(
        "/api/v1/public/invitations/anonymous-invitation/guestbook",
        content='{"guest_name":"Visitor","message":"' + ("x" * 20_000) + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "Request body too large"}


def test_public_submission_validation_and_mass_assignment(guestbook_context):
    client, _, _, tokens = guestbook_context
    assert _submit(client, "personal-invitation", token=tokens["Alice"], message="").status_code == 422
    assert _submit(
        client,
        "personal-invitation",
        token=tokens["Alice"],
        message="   ",
    ).status_code == 422
    assert _submit(
        client,
        "personal-invitation",
        token=tokens["Alice"],
        message="x" * 1001,
    ).status_code == 422
    assert _submit(
        client,
        "anonymous-invitation",
        name="n" * 101,
    ).status_code == 422
    assert _submit(
        client,
        "anonymous-invitation",
        name="Visitor",
        message="Message",
        status="approved",
    ).status_code == 422
    assert _submit(
        client,
        "anonymous-invitation",
        name="Visitor",
        message="Message",
        moderated_by=str(UUID(int=1)),
    ).status_code == 422
    assert _submit(
        client,
        "personal-invitation",
        token=tokens["Alice"],
        guest_id=str(UUID(int=1)),
    ).status_code == 422

    too_large = client.post(
        "/api/v1/public/invitations/personal-invitation/guestbook",
        content='{"guest_token":"' + tokens["Alice"] + '","message":"'
        + ("x" * (17 * 1024))
        + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert too_large.status_code == 413


def test_public_guestbook_only_shows_approved_paged_and_scoped(guestbook_context):
    client, _, _, _ = guestbook_context
    path = "/api/v1/public/invitations/personal-invitation/guestbook"
    page = client.get(path, params={"page": 1, "page_size": 1})
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["has_next"] is False
    assert page.json()["items"][0]["message"] == "Welcome"
    item = page.json()["items"][0]
    assert "guest_id" not in item
    assert "guest_token" not in item
    assert client.get(
        "/api/v1/public/invitations/other-invitation/guestbook"
    ).json()["total"] == 1
    assert client.get(
        "/api/v1/public/invitations/disabled-invitation/guestbook"
    ).status_code == 409
    assert client.get(
        "/api/v1/public/invitations/expired-invitation/guestbook"
    ).status_code == 404


def test_personal_owner_moderates_deletes_and_reads_scoped_statistics(
    guestbook_context,
):
    client, session_factory, ids, _ = guestbook_context
    _login(client, "owner@example.com")
    base = f"/api/v1/invitations/personal/{ids['personal']}/guestbook"
    pending_id = ids["entry_pending"]
    approved = client.patch(
        f"{base}/{pending_id}/moderation",
        headers=_csrf(client),
        json={"status": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    with session_factory() as session:
        entry = session.get(GuestbookEntry, pending_id)
        assert entry.moderated_at is not None
        assert entry.moderated_by == ids["owner"]
        assert entry.is_approved

    hidden = client.patch(
        f"{base}/{pending_id}/moderation",
        headers=_csrf(client),
        json={"status": "hidden"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["status"] == "hidden"
    public_items = client.get(
        "/api/v1/public/invitations/personal-invitation/guestbook"
    ).json()["items"]
    assert all(item["message"] != "Awaiting review" for item in public_items)

    restored = client.patch(
        f"{base}/{pending_id}/moderation",
        headers=_csrf(client),
        json={"status": "approved"},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "approved"

    stats = client.get(f"{base}/statistics")
    assert stats.status_code == 200
    assert stats.json() == {
        "total_messages": 3,
        "pending_messages": 0,
        "approved_messages": 2,
        "hidden_messages": 1,
    }

    cross_invitation = client.get(
        f"/api/v1/invitations/personal/{ids['personal']}/guestbook/"
        f"{ids['entry_approved']}"
    )
    assert cross_invitation.status_code == 200
    foreign_id = client.get(
        f"/api/v1/invitations/personal/{ids['other']}/guestbook/"
        f"{ids['entry_approved']}"
    )
    assert foreign_id.status_code == 404

    delete = client.delete(
        f"{base}/{ids['entry_hidden']}",
        headers=_csrf(client),
    )
    assert delete.status_code == 204
    assert client.get(f"{base}/statistics").json()["total_messages"] == 2
    with session_factory() as session:
        assert session.get(Guest, ids["Alice"]) is not None


def test_guestbook_settings_and_moderation_mass_assignment(guestbook_context):
    client, _, ids, _ = guestbook_context
    _login(client, "owner@example.com")
    path = f"/api/v1/invitations/personal/{ids['personal']}/guestbook/settings"
    current = client.get(path)
    assert current.status_code == 200
    assert current.json()["moderation_required"] is True
    updated = client.patch(
        path,
        headers=_csrf(client),
        json={
            "moderation_required": False,
            "allow_guest_message": False,
            "allow_anonymous": True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["moderation_required"] is False

    invalid = client.patch(
        f"/api/v1/invitations/personal/{ids['personal']}/guestbook/"
        f"{ids['entry_pending']}/moderation",
        headers=_csrf(client),
        json={"status": "approved", "moderated_by": str(ids["owner"])},
    )
    assert invalid.status_code == 422


def test_workspace_roles_and_idor(guestbook_context):
    client, _, ids, _ = guestbook_context
    base = _workspace_path(ids)
    _login(client, "member@example.com")
    member_list = client.get(base)
    assert member_list.status_code == 200
    member_moderation = client.patch(
        f"{base}/{ids['entry_approved']}/moderation",
        headers=_csrf(client),
        json={"status": "hidden"},
    )
    assert member_moderation.status_code == 403
    member_statistics = client.get(f"{base}/statistics")
    assert member_statistics.status_code == 200

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "editor@example.com")
    entry_id = client.get(base).json()["items"][0]["id"]
    editor_moderation = client.patch(
        f"{base}/{entry_id}/moderation",
        headers=_csrf(client),
        json={"status": "hidden"},
    )
    assert editor_moderation.status_code == 200
    editor_settings = client.patch(
        f"{base}/settings",
        headers=_csrf(client),
        json={"moderation_required": False},
    )
    assert editor_settings.status_code == 200

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "outsider@example.com")
    assert client.get(base).status_code == 403


def test_guestbook_moderation_requires_auth_and_rejects_foreign_workspace(
    guestbook_context,
):
    client, _, ids, _ = guestbook_context
    base = _workspace_path(ids)
    assert client.get(base).status_code == 401
    _login(client, "admin@example.com")
    assert client.get(
        _workspace_path(ids, f"/{ids['entry_approved']}")
    ).status_code == 404
