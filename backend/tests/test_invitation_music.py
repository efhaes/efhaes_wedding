from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.dependencies import get_db
from app.core.security import hash_password
from app.main import app
from app.models.client import Client
from app.models.guest import Guest
from app.models.invitation_media import InvitationMedia
from app.models.invitation_section import InvitationSection
from app.models.invitation_view import InvitationView
from app.models.rate_limit_bucket import RateLimitBucket
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.invitation_music.models.invitation_music import InvitationMusic
from app.modules.invitation_music.services.invitation_music_service import (
    InvitationMusicService,
)
from app.modules.invitation_music.services.music_storage_service import (
    MusicStorageService,
)
from app.modules.invitation_music.router import get_music_storage
from app.modules.media.storage import LocalMediaStorage, get_media_storage
from app.modules.invitations.models.invitation import Invitation
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _mp3_frame() -> bytes:
    header = bytes.fromhex("fffb9064")
    return header + bytes(417 - len(header))


def _valid_mp3(frame_count: int = 2) -> bytes:
    return _mp3_frame() * frame_count


@pytest.fixture
def music_context(tmp_path):
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
        Client.__table__,
        Template.__table__,
        Invitation.__table__,
        InvitationSection.__table__,
        InvitationMedia.__table__,
        Guest.__table__,
        InvitationView.__table__,
        InvitationMusic.__table__,
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
            name="Classic",
            slug="classic",
            category="classic",
            configuration={"default_sections": []},
            status="published",
            is_active=True,
        )
        session.add_all([*users.values(), template])
        session.flush()
        workspace = Workspace(
            name="Studio",
            slug="studio",
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
            title="Wedding",
            slug="wedding",
            status="draft",
        )
        other_personal = Invitation(
            owner_id=users["outsider"].id,
            workspace_id=None,
            client_id=None,
            template_id=template.id,
            title="Other",
            slug="other",
            status="draft",
        )
        workspace_invitation = Invitation(
            owner_id=None,
            workspace_id=workspace.id,
            client_id=None,
            template_id=template.id,
            title="Studio Wedding",
            slug="studio-wedding",
            status="draft",
        )
        archived = Invitation(
            owner_id=users["owner"].id,
            workspace_id=None,
            client_id=None,
            template_id=template.id,
            title="Archived",
            slug="archived",
            status="archived",
        )
        session.add_all([personal, other_personal, workspace_invitation, archived])
        session.flush()
        image_key = f"{uuid4().hex}.png"
        image = InvitationMedia(
            invitation_id=personal.id,
            media_type="image",
            storage_key=image_key,
            original_filename="gallery.png",
            mime_type="image/png",
            file_size=16,
        )
        session.add(image)
        session.add(
            InvitationSection(
                invitation_id=personal.id,
                section_key="gallery",
                section_type="gallery",
                title="Gallery",
                content={"media_id": str(image.id)},
                is_enabled=True,
                sort_order=0,
            )
        )
        session.commit()
        ids = {
            "owner": users["owner"].id,
            "admin": users["admin"].id,
            "editor": users["editor"].id,
            "member": users["member"].id,
            "outsider": users["outsider"].id,
            "workspace": workspace.id,
            "personal": personal.id,
            "other_personal": other_personal.id,
            "workspace_invitation": workspace_invitation.id,
            "archived": archived.id,
            "image_id": image.id,
            "image_key": image_key,
        }

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    storage = LocalMediaStorage(tmp_path / "music-storage")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_media_storage] = lambda: storage
    app.dependency_overrides[get_music_storage] = lambda: storage
    with TestClient(app) as client:
        yield client, test_session, ids, storage
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
        json={"email": email, "password": "test-password-123"},
    )
    assert response.status_code == 200


def _upload(client: TestClient, url: str, *, data: bytes | None = None, filename: str = "song.mp3", mime: str = "audio/mpeg"):
    return client.post(
        url,
        headers=_csrf(client),
        files={"file": (filename, _valid_mp3() if data is None else data, mime)},
    )


def _personal_music_url(invitation_id: UUID) -> str:
    return f"/api/v1/invitations/personal/{invitation_id}/music"


def _workspace_music_url(workspace_id: UUID, invitation_id: UUID) -> str:
    return f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/music"


def test_upload_get_settings_public_stream_range_and_delete(music_context):
    client, session_factory, ids, storage = music_context
    _login(client, "owner@example.com")
    url = _personal_music_url(ids["personal"])
    uploaded = _upload(client, url, filename="..\\private-name.mp3")
    assert uploaded.status_code == 201, uploaded.text
    result = uploaded.json()
    assert result["available"] is True
    assert result["is_enabled"] is False
    assert result["autoplay_enabled"] is False
    assert result["mime_type"] == "audio/mpeg"
    assert result["duration_seconds"] > 0
    assert "storage_key" not in result
    assert "path" not in result

    with session_factory() as session:
        music = session.scalar(select(InvitationMusic))
        assert music.storage_key.endswith(".mp3")
        assert music.original_filename == "private-name.mp3"
        assert music.file_size == len(_valid_mp3())
        assert storage.open(music.storage_key).read() == _valid_mp3()
        stored_key = music.storage_key

    settings_response = client.patch(
        f"{url}/settings",
        headers=_csrf(client),
        json={"is_enabled": True, "autoplay_enabled": True},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["is_enabled"] is True
    assert settings_response.json()["autoplay_enabled"] is True

    publish = client.post(
        f"/api/v1/invitations/personal/{ids['personal']}/publish",
        headers=_csrf(client),
    )
    assert publish.status_code == 200
    public_client = TestClient(app)
    public = public_client.get("/api/v1/public/invitations/wedding")
    assert public.status_code == 200
    music_response = public.json()["invitation"]["music"]
    assert music_response["is_enabled"] is True
    assert music_response["autoplay_enabled"] is True
    assert music_response["stream_url"] == "/api/v1/public/invitations/wedding/music"
    assert "storage_key" not in str(public.json())
    assert str(storage.root) not in str(public.json())

    full_stream = public_client.get(music_response["stream_url"])
    assert full_stream.status_code == 200
    assert full_stream.headers["content-type"].startswith("audio/mpeg")
    assert full_stream.headers["accept-ranges"] == "bytes"
    assert full_stream.content == _valid_mp3()
    partial = public_client.get(
        music_response["stream_url"], headers={"Range": "bytes=0-9"}
    )
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-9/{len(_valid_mp3())}"
    assert partial.content == _valid_mp3()[:10]
    invalid_range = public_client.get(
        music_response["stream_url"],
        headers={"Range": "bytes=9999-10000"},
    )
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == f"bytes */{len(_valid_mp3())}"

    deleted = client.delete(url, headers=_csrf(client))
    assert deleted.status_code == 204
    assert public_client.get(music_response["stream_url"]).status_code == 404
    public_client.close()
    assert client.get(f"/api/v1/invitations/personal/{ids['personal']}").status_code == 200
    with session_factory() as session:
        assert session.get(InvitationMusic, UUID(result["id"])) is None
        assert session.get(InvitationMedia, ids["image_id"]) is not None
    assert not storage.root.joinpath(stored_key).exists()


@pytest.mark.parametrize(
    ("filename", "mime", "data"),
    [
        ("song.wav", "audio/mpeg", _valid_mp3()),
        ("song.mp3", "audio/wav", _valid_mp3()),
        ("song.mp3", "audio/mpeg", b"not an mp3"),
        ("song.mp3", "audio/mpeg", b""),
        ("song.mp3", "text/html", b"<script>alert(1)</script>"),
    ],
)
def test_upload_rejects_invalid_music(filename, mime, data, music_context):
    client, _, ids, storage = music_context
    _login(client, "owner@example.com")
    response = _upload(
        client,
        _personal_music_url(ids["personal"]),
        filename=filename,
        mime=mime,
        data=data,
    )
    assert response.status_code in {413, 415, 422}
    assert not list(storage.root.glob("*.mp3"))


def test_upload_size_and_duration_limits_are_enforced(music_context, monkeypatch):
    client, _, ids, storage = music_context
    _login(client, "owner@example.com")
    monkeypatch.setattr(settings, "MUSIC_MAX_FILE_SIZE_MB", 1)
    too_large = _upload(
        client,
        _personal_music_url(ids["personal"]),
        data=_valid_mp3(2600),
    )
    assert too_large.status_code == 413
    assert not list(storage.root.glob("*.mp3"))

    monkeypatch.setattr(settings, "MUSIC_MAX_FILE_SIZE_MB", 10)
    monkeypatch.setattr(settings, "MUSIC_MAX_DURATION_SECONDS", 1)
    too_long = _upload(
        client,
        _personal_music_url(ids["personal"]),
        data=_valid_mp3(50),
    )
    assert too_long.status_code == 413
    assert not list(storage.root.glob("*.mp3"))


def test_music_authorization_personal_workspace_and_idor(music_context):
    client, _, ids, storage = music_context
    personal_url = _personal_music_url(ids["personal"])
    assert _upload(client, personal_url).status_code == 401

    _login(client, "outsider@example.com")
    forbidden = _upload(client, personal_url)
    assert forbidden.status_code == 404

    workspace_url = _workspace_music_url(ids["workspace"], ids["workspace_invitation"])
    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "member@example.com")
    assert _upload(client, workspace_url).status_code == 403
    assert client.get(workspace_url).status_code == 200

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "editor@example.com")
    upload = _upload(client, workspace_url)
    assert upload.status_code == 201
    other_url = _workspace_music_url(uuid4(), ids["workspace_invitation"])
    assert client.get(other_url).status_code == 403


def test_duplicate_upload_replace_and_failed_replace_preserve_old_file(music_context, monkeypatch):
    client, session_factory, ids, storage = music_context
    _login(client, "owner@example.com")
    url = _personal_music_url(ids["personal"])
    original = _upload(client, url)
    assert original.status_code == 201
    duplicate = _upload(client, url)
    assert duplicate.status_code == 409
    original_key = None
    with session_factory() as session:
        original_key = session.get(InvitationMusic, UUID(original.json()["id"])).storage_key

    replace = client.put(
        url,
        headers=_csrf(client),
        files={"file": ("replacement.mp3", _valid_mp3(3), "audio/mpeg")},
    )
    assert replace.status_code == 200, replace.text
    with session_factory() as session:
        replaced = session.get(InvitationMusic, UUID(original.json()["id"]))
        assert replaced.storage_key != original_key
        replacement_key = replaced.storage_key
    assert not storage.root.joinpath(original_key).exists()
    assert storage.root.joinpath(replacement_key).exists()

    def fail_commit(*_args, **_kwargs):
        raise SQLAlchemyError("simulated commit failure")

    with session_factory() as session:
        user = session.get(User, ids["owner"])
        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(SQLAlchemyError):
            InvitationMusicService(
                session,
                MusicStorageService(storage),
            ).replace_personal(
                user,
                ids["personal"],
                "failed.mp3",
                "audio/mpeg",
                BytesIO(_valid_mp3(4)),
            )
    with session_factory() as session:
        current = session.get(InvitationMusic, UUID(original.json()["id"]))
        assert current.storage_key == replacement_key
    assert storage.root.joinpath(replacement_key).exists()
    assert not list(storage.root.glob("*.mp3.tmp"))


def test_settings_delete_empty_read_expired_and_unpublished_public_access(music_context):
    client, session_factory, ids, _ = music_context
    _login(client, "owner@example.com")
    url = _personal_music_url(ids["personal"])
    empty = client.get(url)
    assert empty.status_code == 200
    assert empty.json()["available"] is False

    uploaded = _upload(client, url)
    assert uploaded.status_code == 201
    no_music_settings = client.patch(
        _personal_music_url(ids["other_personal"]) + "/settings",
        headers=_csrf(client),
        json={"is_enabled": True},
    )
    assert no_music_settings.status_code == 404

    enabled = client.patch(
        f"{url}/settings",
        headers=_csrf(client),
        json={"is_enabled": True},
    )
    assert enabled.status_code == 200
    publish = client.post(
        f"/api/v1/invitations/personal/{ids['personal']}/publish",
        headers=_csrf(client),
    )
    assert publish.status_code == 200
    stream_url = "/api/v1/public/invitations/wedding/music"
    assert client.get(stream_url).status_code == 200
    unpublish = client.post(
        f"/api/v1/invitations/personal/{ids['personal']}/unpublish",
        headers=_csrf(client),
    )
    assert unpublish.status_code == 200
    assert client.get(stream_url).status_code == 404

    with session_factory() as session:
        invitation = session.get(Invitation, ids["personal"])
        from datetime import datetime, timedelta, timezone

        invitation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        invitation.first_published_at = None
        invitation.status = "published"
        session.commit()
    assert client.get(stream_url).status_code == 404


def test_upload_db_failure_cleans_new_file_and_range_suffix_works(music_context, monkeypatch):
    _, session_factory, ids, storage = music_context
    with session_factory() as session:
        user = session.get(User, ids["owner"])

        def fail_commit():
            raise SQLAlchemyError("simulated insert failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(SQLAlchemyError):
            InvitationMusicService(
                session,
                MusicStorageService(storage),
            ).upload_personal(
                user,
                ids["personal"],
                "song.mp3",
                "audio/mpeg",
                BytesIO(_valid_mp3()),
            )
    assert not list(storage.root.glob("*.mp3"))
