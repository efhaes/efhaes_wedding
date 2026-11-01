import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.security import hash_password, verify_visitor_identifier
from app.main import app
from app.models.client import Client
from app.models.guest import Guest
from app.models.invitation_media import InvitationMedia
from app.models.invitation_section import InvitationSection
from app.models.invitation_view import InvitationView
from app.models.rate_limit_bucket import RateLimitBucket
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitation_music.models.invitation_music import InvitationMusic
from app.modules.media.storage import LocalMediaStorage, get_media_storage
from app.modules.public_invitations.repositories.public_invitation_repository import (
    PublicInvitationRepository,
)
from app.modules.public_invitations.services.public_invitation_service import (
    guest_token_hash,
)
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def public_context(tmp_path):
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
        Template.__table__,
        Invitation.__table__,
        InvitationSection.__table__,
        InvitationMedia.__table__,
        InvitationMusic.__table__,
        Guest.__table__,
        InvitationView.__table__,
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
        template = Template(
            name="Classic Garden",
            slug="classic-garden",
            category="classic",
            thumbnail_url="https://cdn.example.com/thumbnail.webp",
            preview_image_url="https://cdn.example.com/preview.webp",
            configuration={"design": {"palette": "green"}},
            status="published",
            is_active=True,
        )
        session.add_all([*users.values(), template])
        session.flush()
        workspace = Workspace(
            name="Wedding Studio",
            slug="wedding-studio",
            owner_id=users["owner"].id,
            status="active",
        )
        session.add(workspace)
        session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=users["owner"].id,
                    role="owner",
                    status="active",
                ),
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=users["admin"].id,
                    role="admin",
                    status="active",
                ),
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=users["editor"].id,
                    role="editor",
                    status="active",
                ),
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=users["member"].id,
                    role="member",
                    status="active",
                ),
            ]
        )
        session.flush()
        personal = Invitation(
            owner_id=users["owner"].id,
            workspace_id=None,
            client_id=None,
            template_id=template.id,
            title="Farhan and Aisha",
            slug="farhan-and-aisha",
            status="draft",
        )
        workspace_invitation = Invitation(
            owner_id=None,
            workspace_id=workspace.id,
            client_id=None,
            template_id=template.id,
            title="Studio Invitation",
            slug="studio-invitation",
            status="draft",
        )
        archived = Invitation(
            owner_id=users["owner"].id,
            workspace_id=None,
            client_id=None,
            template_id=template.id,
            title="Archived Invitation",
            slug="archived-invitation",
            status="archived",
        )
        session.add_all([personal, workspace_invitation, archived])
        session.flush()
        media_id = uuid4()
        hidden_media_id = uuid4()
        storage_key = f"{uuid4().hex}.png"
        hidden_storage_key = f"{uuid4().hex}.png"
        media = InvitationMedia(
            id=media_id,
            invitation_id=personal.id,
            media_type="image",
            storage_key=storage_key,
            original_filename="couple.png",
            mime_type="image/png",
            file_size=16,
        )
        hidden_media = InvitationMedia(
            id=hidden_media_id,
            invitation_id=personal.id,
            media_type="image",
            storage_key=hidden_storage_key,
            original_filename="hidden.png",
            mime_type="image/png",
            file_size=16,
        )
        sections = [
            InvitationSection(
                invitation_id=personal.id,
                section_key="cover",
                section_type="cover",
                title="Welcome",
                content={"heading": "The Wedding Of"},
                is_enabled=True,
                sort_order=0,
            ),
            InvitationSection(
                invitation_id=personal.id,
                section_key="gallery",
                section_type="gallery",
                title="Gallery",
                content={
                    "images": [
                        {
                            "media_id": str(media_id),
                            "display_order": 1,
                            "private_caption": "do not expose",
                        }
                    ],
                    "private_note": "do not expose",
                },
                is_enabled=True,
                sort_order=1,
            ),
            InvitationSection(
                invitation_id=personal.id,
                section_key="private-note",
                section_type="event",
                title="Private note",
                content={"secret": "not for guests", "image": {"media_id": str(hidden_media_id)}},
                is_enabled=False,
                sort_order=2,
            ),
            InvitationSection(
                invitation_id=personal.id,
                section_key="rsvp",
                section_type="rsvp",
                title="RSVP",
                content={
                    "allow_response_changes": False,
                    "internal_setting": "private",
                },
                is_enabled=True,
                sort_order=3,
            ),
            InvitationSection(
                invitation_id=personal.id,
                section_key="guestbook",
                section_type="guestbook",
                title="Guestbook",
                content={
                    "moderation_required": True,
                    "internal_setting": "private",
                },
                is_enabled=True,
                sort_order=4,
            ),
            InvitationSection(
                invitation_id=workspace_invitation.id,
                section_key="workspace-cover",
                section_type="cover",
                title="Workspace",
                content={"heading": "Welcome"},
                is_enabled=True,
                sort_order=0,
            ),
        ]
        session.add_all([media, hidden_media, *sections])
        session.commit()
        ids = {
            "owner": users["owner"].id,
            "admin": users["admin"].id,
            "editor": users["editor"].id,
            "member": users["member"].id,
            "outsider": users["outsider"].id,
            "template": template.id,
            "workspace": workspace.id,
            "personal_invitation": personal.id,
            "workspace_invitation": workspace_invitation.id,
            "archived_invitation": archived.id,
            "media": media_id,
            "hidden_media": hidden_media_id,
            "storage_key": storage_key,
            "hidden_storage_key": hidden_storage_key,
        }

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    storage = LocalMediaStorage(tmp_path / "public-tests")
    storage.put(storage_key, b"\x89PNG\r\n\x1a\nPNGdataIEND\xaeB`\x82")
    storage.put(hidden_storage_key, b"\x89PNG\r\n\x1a\nPNGdataIEND\xaeB`\x82")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_media_storage] = lambda: storage
    with TestClient(app) as client:
        yield client, test_session, ids, storage
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
    assert response.status_code == 200


def _publish_personal(client: TestClient, invitation_id: UUID):
    return client.post(
        f"/api/v1/invitations/personal/{invitation_id}/publish",
        headers=_csrf(client),
    )


def test_personal_publish_unpublish_and_public_access(public_context):
    client, session_factory, ids, _ = public_context
    _login(client, "owner@example.com")
    publish = _publish_personal(client, ids["personal_invitation"])
    assert publish.status_code == 200, publish.text
    assert publish.json()["status"] == "published"
    assert publish.json()["first_published_at"]

    public_client = TestClient(app)
    public = public_client.get("/api/v1/public/invitations/farhan-and-aisha")
    assert public.status_code == 200
    body = public.json()
    assert body["invitation"]["title"] == "Farhan and Aisha"
    assert [item["section_key"] for item in body["invitation"]["sections"]] == [
        "cover",
        "gallery",
        "rsvp",
        "guestbook",
    ]
    assert body["guest"] == {"is_personalized": False, "display_name": None}
    encoded = str(body)
    for internal in (
        "owner_id",
        "workspace_id",
        "storage_key",
        "invitation_token",
        "private-note",
        "not for guests",
    ):
        assert internal not in encoded

    unpublish = client.post(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/unpublish",
        headers=_csrf(client),
    )
    assert unpublish.status_code == 200
    assert unpublish.json()["status"] == "draft"
    assert public_client.get("/api/v1/public/invitations/farhan-and-aisha").status_code == 404
    public_client.close()
    with session_factory() as session:
        invitation = session.get(Invitation, ids["personal_invitation"])
        assert invitation.first_published_at is not None
        assert session.scalar(
            select(InvitationSection.id).where(
                InvitationSection.invitation_id == ids["personal_invitation"]
            )
        )
        assert session.scalar(
            select(InvitationView.id).where(
                InvitationView.invitation_id == ids["personal_invitation"]
            )
        )


def test_expired_invitation_is_not_publicly_accessible(public_context):
    client, session_factory, ids, _ = public_context
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200

    with session_factory() as session:
        invitation = session.get(Invitation, ids["personal_invitation"])
        from datetime import datetime, timedelta, timezone

        invitation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        invitation.first_published_at = invitation.expires_at - timedelta(days=1)
        session.commit()

    public = TestClient(app)
    response = public.get("/api/v1/public/invitations/farhan-and-aisha")
    assert response.status_code == 404
    assert response.json() == {"detail": "Invitation not found"}
    public.close()


def test_publish_authorization_workspace_roles_and_archived_state(public_context):
    client, _, ids, _ = public_context
    workspace_path = (
        f"/api/v1/invitations/workspaces/{ids['workspace']}/"
        f"{ids['workspace_invitation']}/publish"
    )
    _login(client, "editor@example.com")
    editor_publish = client.post(workspace_path, headers=_csrf(client))
    assert editor_publish.status_code == 403

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "member@example.com")
    member_publish = client.post(workspace_path, headers=_csrf(client))
    assert member_publish.status_code == 403

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "admin@example.com")
    admin_publish = client.post(workspace_path, headers=_csrf(client))
    assert admin_publish.status_code == 200

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "owner@example.com")
    archived = client.post(
        f"/api/v1/invitations/personal/{ids['archived_invitation']}/publish",
        headers=_csrf(client),
    )
    assert archived.status_code == 409
    assert client.get("/api/v1/public/invitations/archived-invitation").status_code == 404


def test_public_sections_media_filtering_and_view_count_deduplication(public_context):
    client, session_factory, ids, _ = public_context
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200

    response = client.get("/api/v1/public/invitations/farhan-and-aisha")
    assert response.status_code == 200
    sections = response.json()["invitation"]["sections"]
    assert sections[0]["content"] == {"heading": "The Wedding Of"}
    assert sections[1]["content"] == {
        "images": [{"media_id": str(ids["media"]), "display_order": 1}]
    }
    assert sections[2]["content"] == {}
    assert sections[3]["content"] == {}
    section_media = sections[1]["media"]
    assert len(section_media) == 1
    assert section_media[0]["id"] == str(ids["media"])
    assert "storage_key" not in section_media[0]
    media_url = section_media[0]["file_url"]
    assert client.get(media_url).content == b"\x89PNG\r\n\x1a\nPNGdataIEND\xaeB`\x82"
    hidden_url = (
        f"/api/v1/public/invitations/farhan-and-aisha/media/"
        f"{ids['hidden_media']}/file"
    )
    assert client.get(hidden_url).status_code == 404
    unauthenticated = TestClient(app)
    public_media = unauthenticated.get(media_url)
    assert public_media.status_code == 200
    assert public_media.content == b"\x89PNG\r\n\x1a\nPNGdataIEND\xaeB`\x82"
    assert unauthenticated.get(hidden_url).status_code == 404
    unauthenticated.close()

    assert client.get("/api/v1/public/invitations/farhan-and-aisha").status_code == 200
    with session_factory() as session:
        views = list(
            session.scalars(
                select(InvitationView).where(
                    InvitationView.invitation_id == ids["personal_invitation"]
                )
            )
        )
        assert len(views) == 1
        assert views[0].visitor_key_hash
        assert re.fullmatch(r"[a-f0-9]{64}", views[0].visitor_key_hash)
        assert not hasattr(views[0], "ip_address")

    count = client.get(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/views"
    )
    assert count.status_code == 200
    assert count.json()["view_count"] == 1


def test_invitation_view_guest_must_belong_to_same_invitation(public_context):
    _, session_factory, ids, _ = public_context
    with session_factory() as session:
        guest = Guest(
            invitation_id=ids["personal_invitation"],
            name="Personal guest",
            invitation_token=uuid4().hex,
        )
        session.add(guest)
        session.flush()
        session.add(
            InvitationView(
                invitation_id=ids["personal_invitation"],
                guest_id=guest.id,
                visitor_key_hash=uuid4().hex,
            )
        )
        session.add(
            InvitationView(
                invitation_id=ids["personal_invitation"],
                guest_id=None,
                visitor_key_hash=None,
            )
        )
        session.commit()
        guest_id = guest.id

    with session_factory() as session:
        other_guest = Guest(
            invitation_id=ids["workspace_invitation"],
            name="Other invitation guest",
            invitation_token=uuid4().hex,
        )
        session.add(other_guest)
        session.flush()
        session.add(
            InvitationView(
                invitation_id=ids["personal_invitation"],
                guest_id=other_guest.id,
                visitor_key_hash=uuid4().hex,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_factory() as session:
        valid_view = session.scalar(
            select(InvitationView).where(InvitationView.guest_id == guest_id)
        )
        assert valid_view is not None


def test_personalized_guest_link_is_hashed_scoped_and_regeneratable(public_context):
    client, session_factory, ids, _ = public_context
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200
    created = client.post(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests",
        headers=_csrf(client),
        json={"name": "Bapak Ahmad", "category": "Family"},
    )
    assert created.status_code == 201
    assert "invitation_token" not in created.json()["guest"]
    personal_url = created.json()["invitation_url"]
    token = parse_qs(urlparse(personal_url).fragment)["to"][0]
    assert len(token) >= 40

    with session_factory() as session:
        guest = session.scalar(select(Guest))
        assert guest.invitation_token == guest_token_hash(token)
        assert guest.invitation_token != token
        guest_id = guest.id

    personalized = client.get(
        "/api/v1/public/invitations/farhan-and-aisha",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert personalized.status_code == 200
    assert personalized.json()["guest"] == {
        "is_personalized": True,
        "display_name": "Bapak Ahmad",
    }

    invalid = client.get(
        "/api/v1/public/invitations/farhan-and-aisha?guest_token=invalid-token"
    )
    assert invalid.status_code == 200
    assert invalid.json()["guest"] == {"is_personalized": False, "display_name": None}

    regenerated = client.post(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests/"
        f"{guest_id}/regenerate-link",
        headers=_csrf(client),
    )
    assert regenerated.status_code == 200
    new_token = parse_qs(urlparse(regenerated.json()["invitation_url"]).fragment)["to"][0]
    assert new_token != token
    assert client.get(
        f"/api/v1/public/invitations/farhan-and-aisha?guest_token={token}"
    ).json()["guest"]["is_personalized"] is False
    assert client.get(
        "/api/v1/public/invitations/farhan-and-aisha",
        headers={"Authorization": f"Bearer {new_token}"},
    ).json()["guest"]["display_name"] == "Bapak Ahmad"


def test_guest_management_crud_does_not_expose_contact_or_token(public_context):
    client, _, ids, _ = public_context
    _login(client, "owner@example.com")
    guests_url = f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests"
    created = client.post(
        guests_url,
        headers=_csrf(client),
        json={"name": "Aisha", "category": "Friends", "email": "private@example.com"},
    )
    assert created.status_code == 422

    created = client.post(
        guests_url,
        headers=_csrf(client),
        json={"name": "Aisha", "category": "Friends"},
    )
    assert created.status_code == 201
    guest_id = created.json()["guest"]["id"]
    listed = client.get(guests_url)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert "invitation_token" not in listed.text
    assert "email" not in listed.text

    updated = client.patch(
        f"{guests_url}/{guest_id}",
        headers=_csrf(client),
        json={"name": "Aisha Rahman"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Aisha Rahman"

    deleted = client.delete(
        f"{guests_url}/{guest_id}",
        headers=_csrf(client),
    )
    assert deleted.status_code == 204
    assert client.get(f"{guests_url}/{guest_id}").status_code == 404


def test_workspace_guest_roles_and_idor_protection(public_context):
    client, _, ids, _ = public_context
    guests_url = (
        f"/api/v1/invitations/workspaces/{ids['workspace']}/"
        f"{ids['workspace_invitation']}/guests"
    )
    _login(client, "member@example.com")
    assert client.get(guests_url).status_code == 200
    member_create = client.post(
        guests_url,
        headers=_csrf(client),
        json={"name": "Should not be added"},
    )
    assert member_create.status_code == 403

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "editor@example.com")
    editor_create = client.post(
        guests_url,
        headers=_csrf(client),
        json={"name": "Studio Guest"},
    )
    assert editor_create.status_code == 201
    guest_id = editor_create.json()["guest"]["id"]

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "outsider@example.com")
    assert client.get(guests_url).status_code == 403
    assert client.get(f"{guests_url}/{guest_id}").status_code == 403


def test_guest_tokens_are_scoped_to_invitation_and_unpublished_media_is_private(public_context):
    client, _, ids, _ = public_context
    _login(client, "owner@example.com")
    guest = client.post(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests",
        headers=_csrf(client),
        json={"name": "Private Guest"},
    )
    token = parse_qs(urlparse(guest.json()["invitation_url"]).fragment)["to"][0]
    workspace_publish = client.post(
        f"/api/v1/invitations/workspaces/{ids['workspace']}/"
        f"{ids['workspace_invitation']}/publish",
        headers=_csrf(client),
    )
    assert workspace_publish.status_code == 200
    assert client.get(
        f"/api/v1/public/invitations/studio-invitation?guest_token={token}"
    ).json()["guest"]["is_personalized"] is False

    unpublished_file = (
        f"/api/v1/public/invitations/farhan-and-aisha/media/{ids['media']}/file"
    )
    assert client.get(unpublished_file).status_code == 404


def test_view_tracking_failure_does_not_block_public_invitation(public_context, monkeypatch):
    client, _, ids, _ = public_context
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200

    def fail_record(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("simulated tracking failure")

    monkeypatch.setattr(PublicInvitationRepository, "record_view", fail_record)
    response = client.get("/api/v1/public/invitations/farhan-and-aisha")
    assert response.status_code == 200
    assert response.json()["invitation"]["slug"] == "farhan-and-aisha"


def test_modified_visitor_cookie_is_replaced_and_guest_token_query_is_not_logged(
    public_context, caplog
):
    client, session_factory, ids, _ = public_context
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200
    token = "guest-token-not-for-logs-0123456789"
    response = client.get(
        f"/api/v1/public/invitations/farhan-and-aisha?guest_token={token}"
    )
    assert response.status_code == 200
    assert token not in caplog.text
    assert response.headers["referrer-policy"] == "no-referrer"
    cookie = response.cookies["public_viewer"]
    assert verify_visitor_identifier(cookie) is not None

    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    client.cookies.clear()
    client.cookies.set(
        "public_viewer",
        tampered,
        path="/api/v1/public/invitations",
    )
    second = client.get("/api/v1/public/invitations/farhan-and-aisha")
    assert second.status_code == 200
    assert verify_visitor_identifier(second.cookies["public_viewer"]) is not None
    assert second.cookies["public_viewer"] != tampered
    with session_factory() as session:
        assert session.query(InvitationView).filter_by(
            invitation_id=ids["personal_invitation"]
        ).count() == 2


def test_public_reads_and_guest_access_are_rate_limited(public_context, monkeypatch):
    from app.core.config import settings

    client, _, ids, _ = public_context
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_REQUESTS", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_GUEST_ACCESS_REQUESTS", 1)
    _login(client, "owner@example.com")
    assert _publish_personal(client, ids["personal_invitation"]).status_code == 200

    public_path = "/api/v1/public/invitations/farhan-and-aisha"
    assert client.get(public_path).status_code == 200
    assert client.get(public_path).status_code == 429

    guests_path = (
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests"
    )
    assert client.get(guests_path).status_code == 200
    assert client.get(guests_path).status_code == 429


def test_concurrent_view_upserts_remain_unique(tmp_path):
    database_path = tmp_path / "view-concurrency.sqlite"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"timeout": 30},
        pool_size=12,
        max_overflow=0,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    tables = [
        User.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Client.__table__,
        Template.__table__,
        Invitation.__table__,
        Guest.__table__,
        InvitationView.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as session:
        user = User(name="Owner", email="owner@view-test.invalid", password_hash="test")
        template = Template(
            name="Template",
            slug="view-test-template",
            category="test",
            configuration={},
            status="published",
            is_active=True,
        )
        session.add_all([user, template])
        session.flush()
        invitation = Invitation(
            owner_id=user.id,
            template_id=template.id,
            title="Concurrent View Test",
            slug="concurrent-view-test",
            status="published",
        )
        session.add(invitation)
        session.commit()
        invitation_id = invitation.id

    barrier = Barrier(12)

    def record_view(_index):
        with test_session() as session:
            barrier.wait(timeout=20)
            PublicInvitationRepository(session).record_view(
                invitation_id,
                "b" * 64,
                None,
            )

    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            list(executor.map(record_view, range(12)))
        with test_session() as session:
            assert session.query(InvitationView).filter_by(
                invitation_id=invitation_id,
                visitor_key_hash="b" * 64,
            ).count() == 1
    finally:
        engine.dispose()


def test_public_data_is_404_for_unknown_slug_and_guest_endpoints_require_login(public_context):
    client, _, ids, _ = public_context
    assert client.get("/api/v1/public/invitations/not-a-real-slug").status_code == 404
    guest_url = f"/api/v1/invitations/personal/{ids['personal_invitation']}/guests"
    assert client.get(guest_url).status_code == 401
