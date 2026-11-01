from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.security import hash_password
from app.main import app
from app.models.client import Client
from app.models.guest import Guest
from app.models.invitation_section import InvitationSection
from app.models.rsvp import RSVP
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
def rsvp_context():
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
        RSVP.__table__,
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
                owner_id=users["owner"].id if name != "workspace" else None,
                workspace_id=workspace.id if name == "workspace" else None,
                client_id=None,
                template_id=template.id,
                title=f"{name.title()} Invitation",
                slug=f"{name}-invitation",
                status=state,
                first_published_at=now - timedelta(days=2) if state == "published" else None,
                expires_at=(
                    now - timedelta(days=1)
                    if name == "expired"
                    else None
                ),
            )
            for name, state in (
                ("personal", "published"),
                ("workspace", "published"),
                ("other", "published"),
                ("disabled", "published"),
                ("draft", "draft"),
                ("archived", "archived"),
                ("expired", "published"),
            )
        }
        invitations["archived"].first_published_at = now - timedelta(days=2)
        session.add_all(invitations.values())
        session.flush()
        for name, invitation in invitations.items():
            if name in ("personal", "workspace", "other", "disabled"):
                session.add(
                    InvitationSection(
                        invitation_id=invitation.id,
                        section_key="rsvp",
                        section_type="rsvp",
                        title="RSVP",
                        content={
                            "allow_response_changes": True,
                            "max_attendees_per_guest": 3,
                        },
                        is_enabled=name != "disabled",
                        sort_order=0,
                    )
                )
        guests = {}
        for guest_name, invitation_name, token in (
            ("Alice", "personal", "alice-token-0123456789-abcdefghijk"),
            ("Bob", "other", "bob-token-0123456789-abcdefghijk"),
            ("Studio Guest", "workspace", "studio-token-0123456789-abcdefgh"),
        ):
            guest = Guest(
                invitation_id=invitations[invitation_name].id,
                name=guest_name,
                email=f"{guest_name.lower().replace(' ', '.')}@example.com",
                invitation_token=guest_token_hash(token),
                checkin_status="pending",
            )
            session.add(guest)
            guests[guest_name] = (guest, token)
        session.commit()
        ids = {
            "owner": users["owner"].id,
            "admin": users["admin"].id,
            "editor": users["editor"].id,
            "member": users["member"].id,
            "outsider": users["outsider"].id,
            "workspace_id": workspace.id,
            **{name: invitation.id for name, invitation in invitations.items()},
            **{name: guest.id for name, (guest, _) in guests.items()},
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


def _public_submit(
    client: TestClient,
    slug: str,
    token: str,
    *,
    attendance_status: str = "attending",
    number_of_attendees: int | None = 2,
    message: str | None = "See you soon",
    use_authorization: bool = False,
):
    headers = {"Authorization": f"Bearer {token}"} if use_authorization else None
    payload = {
        "attendance_status": attendance_status,
        "number_of_attendees": number_of_attendees,
        "message": message,
    }
    if not use_authorization:
        payload["guest_token"] = token
    return client.post(
        f"/api/v1/public/invitations/{slug}/rsvp",
        headers=headers,
        json=payload,
    )


def test_public_rsvp_create_update_and_unique_guest_record(rsvp_context):
    client, session_factory, ids, tokens = rsvp_context
    first = _public_submit(client, "personal-invitation", tokens["Alice"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["attendance_status"] == "attending"
    assert body["number_of_attendees"] == 2
    assert "guest_name" not in body
    assert "guest_id" not in body
    assert "invitation_id" not in body
    assert "guest_token" not in body
    assert "email" not in body
    authorization_only = _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        use_authorization=True,
    )
    assert authorization_only.status_code == 200
    with session_factory() as session:
        original_rsvp_id = session.query(RSVP).filter_by(
            invitation_id=ids["personal"],
            guest_id=ids["Alice"],
        ).one().id

    updated = _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        attendance_status="not_attending",
        number_of_attendees=0,
        message="Sorry, cannot attend",
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["attendance_status"] == "not_attending"
    assert updated.json()["number_of_attendees"] == 0

    with session_factory() as session:
        records = session.query(RSVP).filter_by(
            invitation_id=ids["personal"],
            guest_id=ids["Alice"],
        )
        assert records.count() == 1
        record = records.one()
        assert record.id == original_rsvp_id
        assert record.guest_count == 0


def test_public_rsvp_rejects_bad_tokens_and_nonreceiving_invitations(rsvp_context):
    client, _, _, tokens = rsvp_context
    invalid = _public_submit(client, "personal-invitation", "x" * 40)
    assert invalid.status_code == 404

    cross_invitation = _public_submit(
        client,
        "personal-invitation",
        tokens["Bob"],
    )
    assert cross_invitation.status_code == 404

    for slug in ("draft-invitation", "archived-invitation", "expired-invitation"):
        response = _public_submit(client, slug, tokens["Alice"])
        assert response.status_code == 404

    disabled = _public_submit(client, "disabled-invitation", tokens["Alice"])
    assert disabled.status_code == 409


def test_public_rsvp_rejects_invalid_counts_and_mass_assignment(rsvp_context):
    client, _, _, tokens = rsvp_context
    url = "/api/v1/public/invitations/personal-invitation/rsvp"
    no_token = client.post(
        url,
        json={"attendance_status": "attending", "number_of_attendees": 1},
    )
    assert no_token.status_code == 401

    fake_identity = client.post(
        url,
        json={
            "guest_token": tokens["Alice"],
            "attendance_status": "attending",
            "number_of_attendees": 1,
            "guest_id": str(uuid4()),
        },
    )
    assert fake_identity.status_code == 422
    fake_invitation = client.post(
        url,
        json={
            "guest_token": tokens["Alice"],
            "attendance_status": "attending",
            "number_of_attendees": 1,
            "invitation_id": str(uuid4()),
        },
    )
    assert fake_invitation.status_code == 422

    for count in (0, -1, 4):
        response = _public_submit(
            client,
            "personal-invitation",
            tokens["Alice"],
            number_of_attendees=count,
        )
        assert response.status_code == 422

    not_attending_with_people = _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        attendance_status="not_attending",
        number_of_attendees=1,
    )
    assert not_attending_with_people.status_code == 422

    long_message = _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        message="x" * 1001,
    )
    assert long_message.status_code == 422


def test_public_rsvp_rate_limit_and_body_size(rsvp_context, monkeypatch):
    from app.core.config import settings

    client, _, _, tokens = rsvp_context
    monkeypatch.setattr(settings, "RATE_LIMIT_SUBMISSION_REQUESTS", 1)
    first = _public_submit(client, "personal-invitation", tokens["Alice"])
    limited = _public_submit(client, "personal-invitation", tokens["Alice"])
    assert first.status_code == 200
    assert limited.status_code == 429

    oversized = client.post(
        "/api/v1/public/invitations/personal-invitation/rsvp",
        content='{"guest_token":"' + ("x" * 20_000) + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "Request body too large"}


def test_settings_control_changes_limit_and_deadline(rsvp_context):
    client, _, ids, tokens = rsvp_context
    _login(client, "owner@example.com")
    path = f"/api/v1/invitations/personal/{ids['personal']}/rsvps/settings"
    read = client.get(path)
    assert read.status_code == 200
    assert read.json()["max_attendees_per_guest"] == 3

    excessive_limit = client.patch(
        path,
        headers=_csrf(client),
        json={"max_attendees_per_guest": 11},
    )
    assert excessive_limit.status_code == 422

    update = client.patch(
        path,
        headers=_csrf(client),
        json={"allow_response_changes": False, "is_enabled": True},
    )
    assert update.status_code == 200, update.text
    initial = _public_submit(client, "personal-invitation", tokens["Alice"])
    assert initial.status_code == 200
    blocked_change = _public_submit(client, "personal-invitation", tokens["Alice"])
    assert blocked_change.status_code == 409

    deadline = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    update_deadline = client.patch(
        path,
        headers=_csrf(client),
        json={"allow_response_changes": True, "deadline_at": deadline},
    )
    assert update_deadline.status_code == 200
    expired_submission = _public_submit(client, "personal-invitation", tokens["Alice"])
    assert expired_submission.status_code == 409


def test_rsvp_management_pagination_filter_and_statistics(rsvp_context):
    client, _, ids, tokens = rsvp_context
    assert _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        attendance_status="attending",
        number_of_attendees=2,
    ).status_code == 200

    _login(client, "owner@example.com")
    base = f"/api/v1/invitations/personal/{ids['personal']}/rsvps"
    listing = client.get(base, params={"page": 1, "page_size": 1, "sort_by": "responded_at"})
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["guest_name"] == "Alice"
    filtered = client.get(base, params={"status": "not_attending"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0

    stats = client.get(f"{base}/statistics")
    assert stats.status_code == 200
    assert stats.json() == {
        "total_guests": 1,
        "responded_guests": 1,
        "not_responded_guests": 0,
        "attending_guests": 1,
        "not_attending_guests": 0,
        "total_attending_people": 2,
    }

    rsvp_id = UUID(listing.json()["items"][0]["id"])
    other_invitation = client.get(
        f"/api/v1/invitations/personal/{ids['other']}/rsvps/{rsvp_id}"
    )
    assert other_invitation.status_code == 404


def test_owner_can_manually_update_and_delete_rsvp(rsvp_context):
    client, _, ids, tokens = rsvp_context
    submitted = _public_submit(
        client,
        "personal-invitation",
        tokens["Alice"],
        number_of_attendees=2,
    )
    assert submitted.status_code == 200
    _login(client, "owner@example.com")

    base = f"/api/v1/invitations/personal/{ids['personal']}/rsvps"
    rsvp_id = client.get(base).json()["items"][0]["id"]
    updated = client.patch(
        f"{base}/{rsvp_id}",
        headers=_csrf(client),
        json={"attendance_status": "not_attending", "number_of_attendees": 0},
    )
    assert updated.status_code == 200
    assert updated.json()["attendance_status"] == "not_attending"
    assert updated.json()["number_of_attendees"] == 0

    deleted = client.delete(f"{base}/{rsvp_id}", headers=_csrf(client))
    assert deleted.status_code == 204
    assert client.get(base).json()["total"] == 0
    assert client.get(f"{base}/statistics").json()["not_responded_guests"] == 1


def test_workspace_rsvp_permissions_and_scoping(rsvp_context):
    client, _, ids, tokens = rsvp_context
    submitted = _public_submit(
        client,
        "workspace-invitation",
        tokens["Studio Guest"],
        number_of_attendees=3,
    )
    assert submitted.status_code == 200, submitted.text

    base = (
        f"/api/v1/invitations/workspaces/{ids['workspace_id']}/"
        f"{ids['workspace']}/rsvps"
    )
    _login(client, "editor@example.com")
    editor_list = client.get(base)
    assert editor_list.status_code == 200
    rsvp_id = editor_list.json()["items"][0]["id"]
    editor_update = client.patch(
        f"{base}/{rsvp_id}",
        headers=_csrf(client),
        json={"attendance_status": "not_attending", "number_of_attendees": 0},
    )
    assert editor_update.status_code == 403
    editor_settings = client.get(f"{base}/settings")
    assert editor_settings.status_code == 200

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "admin@example.com")
    admin_update = client.patch(
        f"{base}/{rsvp_id}",
        headers=_csrf(client),
        json={"attendance_status": "not_attending", "number_of_attendees": 0},
    )
    assert admin_update.status_code == 200
    assert admin_update.json()["number_of_attendees"] == 0


def test_workspace_member_and_unauthenticated_access_are_denied(rsvp_context):
    client, _, ids, _ = rsvp_context
    base = (
        f"/api/v1/invitations/workspaces/{ids['workspace_id']}/"
        f"{ids['workspace']}/rsvps"
    )
    unauthenticated = client.get(base)
    assert unauthenticated.status_code == 401

    _login(client, "member@example.com")
    member_list = client.get(base)
    assert member_list.status_code == 200
    member_settings = client.patch(
        f"{base}/settings",
        headers=_csrf(client),
        json={"is_enabled": False},
    )
    assert member_settings.status_code == 403

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "outsider@example.com")
    outsider_list = client.get(base)
    assert outsider_list.status_code == 403


def test_database_constraints_reject_duplicate_guest_rsvp_and_cross_invitation_link(
    rsvp_context,
):
    _, session_factory, ids, _ = rsvp_context
    with session_factory() as session:
        session.add(
            RSVP(
                invitation_id=ids["personal"],
                guest_id=ids["Alice"],
                guest_name="Alice",
                attendance_status="attending",
                guest_count=1,
                responded_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        session.add(
            RSVP(
                invitation_id=ids["personal"],
                guest_id=ids["Alice"],
                guest_name="Alice",
                attendance_status="attending",
                guest_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        existing = session.query(RSVP).filter_by(
            invitation_id=ids["personal"],
            guest_id=ids["Alice"],
        ).one()
        rsvp_id = existing.id
        session.delete(session.get(Guest, ids["Alice"]))
        session.commit()
        assert session.query(RSVP).filter_by(id=rsvp_id).count() == 0

        session.add(
            RSVP(
                invitation_id=ids["personal"],
                guest_id=ids["Bob"],
                guest_name="Bob",
                attendance_status="attending",
                guest_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
