import re
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image
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
from app.models.invitation_media import InvitationMedia
from app.models.invitation_section import InvitationSection
from app.models.rate_limit_bucket import RateLimitBucket
from app.models.workspace import Workspace, WorkspaceMember
from app.modules.auth.models.refresh_token import RefreshToken
from app.modules.invitations.models.invitation import Invitation
from app.modules.invitations.services.invitation_service import InvitationService
from app.modules.media.services.media_service import MediaService
from app.modules.media.storage import LocalMediaStorage, get_media_storage
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def editor_context(tmp_path):
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
            for name in ("owner", "editor", "member", "outsider")
        }
        template = Template(
            name="Garden",
            slug="garden",
            category="classic",
            configuration={
                "design": {"palette": "green"},
                "default_sections": [
                    {
                        "key": "main_cover",
                        "type": "cover",
                        "title": "Welcome",
                        "is_enabled": True,
                        "configuration": {"heading": "The Wedding Of"},
                    },
                    {
                        "key": "our_gallery",
                        "type": "gallery",
                        "title": "Our Gallery",
                        "is_enabled": False,
                        "configuration": {"images": []},
                    },
                ],
            },
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
        memberships = [
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=users["owner"].id,
                role="owner",
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
        session.add_all(memberships)
        session.flush()
        personal = Invitation(
            owner_id=users["owner"].id,
            workspace_id=None,
            client_id=None,
            template_id=template.id,
            title="Personal Invitation",
            slug="personal-invitation",
            status="draft",
        )
        workspace_invitation = Invitation(
            owner_id=None,
            workspace_id=workspace.id,
            client_id=None,
            template_id=template.id,
            title="Workspace Invitation",
            slug="workspace-invitation",
            status="draft",
        )
        session.add_all([personal, workspace_invitation])
        session.flush()
        personal_id = personal.id
        workspace_invitation_id = workspace_invitation.id
        invitation_service = InvitationService(session)
        invitation_service._persist_new(personal)
        invitation_service._persist_new(workspace_invitation)
        ids = {
            "owner": users["owner"].id,
            "editor": users["editor"].id,
            "member": users["member"].id,
            "outsider": users["outsider"].id,
            "workspace": workspace.id,
            "template": template.id,
            "personal_invitation": personal_id,
            "workspace_invitation": workspace_invitation_id,
        }
        session.commit()

    def override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    storage = LocalMediaStorage(tmp_path / "test-media")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_media_storage] = lambda: storage
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


def _image_bytes(image_format: str, size: tuple[int, int] = (2, 2)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(20, 100, 180)).save(output, format=image_format)
    return output.getvalue()


def _personal_sections_url(invitation_id: UUID) -> str:
    return f"/api/v1/invitations/personal/{invitation_id}/sections"


def _workspace_sections_url(workspace_id: UUID, invitation_id: UUID) -> str:
    return f"/api/v1/invitations/workspaces/{workspace_id}/{invitation_id}/sections"


def _upload_personal(client: TestClient, invitation_id: UUID, filename: str, data: bytes, mime: str):
    return client.post(
        f"/api/v1/invitations/personal/{invitation_id}/media",
        headers=_csrf(client),
        files={"upload": (filename, data, mime)},
    )


def test_invitation_initializes_independent_default_sections_and_retry_is_idempotent(
    editor_context,
):
    _, session_factory, ids, _ = editor_context
    with session_factory() as session:
        sections = list(
            session.scalars(
                select(InvitationSection)
                .where(
                    InvitationSection.invitation_id == ids["personal_invitation"]
                )
                .order_by(InvitationSection.sort_order)
            )
        )
        template_before = session.get(Template, ids["template"]).configuration.copy()
        invitation = session.get(Invitation, ids["personal_invitation"])
        InvitationService(session)._persist_new(invitation)
        retried_sections = list(
            session.scalars(
                select(InvitationSection).where(
                    InvitationSection.invitation_id == ids["personal_invitation"]
                )
            )
        )
        template_after = session.get(Template, ids["template"]).configuration

    assert len(sections) == 2
    assert sections[0].section_key == "main_cover"
    assert sections[0].section_type == "cover"
    assert sections[0].title == "Welcome"
    assert sections[0].content == {"heading": "The Wedding Of"}
    assert sections[1].is_enabled is False
    assert len(retried_sections) == len(sections)
    assert template_after == template_before


def test_personal_invitation_create_endpoint_copies_template_sections(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    response = client.post(
        "/api/v1/invitations/personal",
        headers=_csrf(client),
        json={"title": "New Wedding", "template_id": str(ids["template"])},
    )
    assert response.status_code == 201
    sections = client.get(
        _personal_sections_url(UUID(response.json()["id"]))
    )
    assert sections.status_code == 200
    assert [item["section_key"] for item in sections.json()] == [
        "main_cover",
        "our_gallery",
    ]


def test_section_update_enable_reorder_and_personal_ownership(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    url = _personal_sections_url(ids["personal_invitation"])
    listed = client.get(url)
    assert listed.status_code == 200
    sections = listed.json()
    assert [item["section_key"] for item in sections] == ["main_cover", "our_gallery"]

    updated = client.patch(
        f"{url}/{sections[0]['id']}",
        headers=_csrf(client),
        json={
            "content": {"heading": "Our Day", "title": "A nested content title"},
            "expected_updated_at": sections[0]["updated_at"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["content"]["heading"] == "Our Day"

    enabled = client.patch(
        f"{url}/{sections[1]['id']}/enabled",
        headers=_csrf(client),
        json={"is_enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_enabled"] is True

    reordered = client.put(
        f"{url}/reorder",
        headers=_csrf(client),
        json={"section_ids": [sections[1]["id"], sections[0]["id"]]},
    )
    assert reordered.status_code == 200
    assert [item["sort_order"] for item in reordered.json()] == [0, 1]

    outsider = TestClient(app)
    try:
        _login(outsider, "outsider@example.com")
        denied = outsider.get(url)
        assert denied.status_code == 404
    finally:
        outsider.close()


def test_section_rejects_bad_content_duplicate_order_and_stale_version(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    url = _personal_sections_url(ids["personal_invitation"])
    sections = client.get(url).json()
    section_id = sections[0]["id"]

    bad_content = client.patch(
        f"{url}/{section_id}",
        headers=_csrf(client),
        json={"content": {"invitation_id": str(ids["personal_invitation"])}},
    )
    assert bad_content.status_code == 422

    duplicate_order = client.put(
        f"{url}/reorder",
        headers=_csrf(client),
        json={"section_ids": [section_id, section_id]},
    )
    assert duplicate_order.status_code == 422

    stale = client.patch(
        f"{url}/{section_id}",
        headers=_csrf(client),
        json={
            "content": {"heading": "Stale update"},
            "expected_updated_at": "2000-01-01T00:00:00Z",
        },
    )
    assert stale.status_code == 409


def test_section_reset_restores_template_defaults(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    url = _personal_sections_url(ids["personal_invitation"])
    section = client.get(url).json()[0]
    changed = client.patch(
        f"{url}/{section['id']}",
        headers=_csrf(client),
        json={"content": {"heading": "Changed"}},
    )
    assert changed.status_code == 200
    reset = client.post(
        f"{url}/{section['id']}/reset",
        headers=_csrf(client),
    )
    assert reset.status_code == 200
    assert reset.json()["content"] == {"heading": "The Wedding Of"}


def test_workspace_editor_can_edit_but_member_is_read_only(editor_context):
    client, _, ids, _ = editor_context
    url = _workspace_sections_url(ids["workspace"], ids["workspace_invitation"])
    _login(client, "member@example.com")
    member_read = client.get(url)
    assert member_read.status_code == 200
    section_id = member_read.json()[0]["id"]
    member_write = client.patch(
        f"{url}/{section_id}",
        headers=_csrf(client),
        json={"content": {"heading": "Blocked"}},
    )
    assert member_write.status_code == 403

    client.post("/api/v1/auth/logout", headers=_csrf(client))
    _login(client, "editor@example.com")
    edit = client.patch(
        f"{url}/{section_id}",
        headers=_csrf(client),
        json={"content": {"heading": "Edited by workspace editor"}},
    )
    assert edit.status_code == 200


def test_media_upload_formats_listing_file_serving_and_safe_storage_key(editor_context):
    client, session_factory, ids, storage = editor_context
    _login(client, "owner@example.com")
    samples = [
        ("..\\unsafe.jpg", _image_bytes("JPEG"), "image/jpeg"),
        ("photo.png", _image_bytes("PNG"), "image/png"),
        ("photo.webp", _image_bytes("WEBP"), "image/webp"),
    ]
    for filename, data, mime in samples:
        response = _upload_personal(
            client, ids["personal_invitation"], filename, data, mime
        )
        assert response.status_code == 201
        assert response.json()["file_url"].endswith("/file")
        assert "\\" not in response.json()["file_url"]
        with session_factory() as session:
            media = session.get(InvitationMedia, UUID(response.json()["id"]))
            assert re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", media.storage_key)
            assert media.original_filename == filename.replace("\\", "/").rsplit("/", 1)[-1]
            assert storage.path(media.storage_key).read_bytes() == data

        file_response = client.get(response.json()["file_url"])
        assert file_response.status_code == 200
        assert file_response.content == data
        assert file_response.headers["x-content-type-options"] == "nosniff"

    listing = client.get(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/media"
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 3


@pytest.mark.parametrize(
    ("filename", "data", "mime", "expected_status"),
    [
        ("wrong.jpg", b"\x89PNG\r\n\x1a\nxIEND\xaeB`\x82", "image/jpeg", 422),
        ("wrong.png", b"\xff\xd8\xffx\xff\xd9", "image/jpeg", 422),
        ("file.html", b"<html></html>", "text/html", 415),
        ("fake.webp", b"not an image", "image/webp", 422),
    ],
)
def test_media_upload_rejects_mime_extension_and_signature_mismatch(
    editor_context, filename, data, mime, expected_status
):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    result = _upload_personal(client, ids["personal_invitation"], filename, data, mime)
    assert result.status_code == expected_status


def test_media_upload_limits_size_and_upload_requires_authentication(editor_context):
    client, _, ids, _ = editor_context
    unauthenticated = _upload_personal(
        client,
        ids["personal_invitation"],
        "photo.png",
        b"\x89PNG\r\n\x1a\nxIEND\xaeB`\x82",
        "image/png",
    )
    assert unauthenticated.status_code == 401

    _login(client, "owner@example.com")
    too_large = _upload_personal(
        client,
        ids["personal_invitation"],
        "large.jpg",
        b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024) + b"\xff\xd9",
        "image/jpeg",
    )
    assert too_large.status_code == 413


def test_media_upload_is_rate_limited(editor_context, monkeypatch):
    from app.core.config import settings

    client, _, ids, _ = editor_context
    monkeypatch.setattr(settings, "RATE_LIMIT_MEDIA_UPLOAD_REQUESTS", 1)
    _login(client, "owner@example.com")

    first = _upload_personal(
        client,
        ids["personal_invitation"],
        "first.png",
        _image_bytes("PNG"),
        "image/png",
    )
    limited = _upload_personal(
        client,
        ids["personal_invitation"],
        "second.png",
        _image_bytes("PNG"),
        "image/png",
    )

    assert first.status_code == 201
    assert limited.status_code == 429


def test_media_upload_rejects_corrupt_and_excessively_wide_images(editor_context):
    client, _, ids, storage = editor_context
    _login(client, "owner@example.com")

    corrupt = _upload_personal(
        client,
        ids["personal_invitation"],
        "corrupt.png",
        b"\x89PNG\r\n\x1a\nnot-a-pngIEND\xaeB`\x82",
        "image/png",
    )
    oversized_dimensions = _upload_personal(
        client,
        ids["personal_invitation"],
        "wide.png",
        _image_bytes("PNG", (10_001, 1)),
        "image/png",
    )

    assert corrupt.status_code == 422
    assert oversized_dimensions.status_code == 422
    assert not list(storage.root.glob("*.png"))


def test_media_upload_rejects_excessive_total_pixel_count(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    output = BytesIO()
    Image.new("1", (8_000, 5_001), color=0).save(output, format="PNG")

    response = _upload_personal(
        client,
        ids["personal_invitation"],
        "too-many-pixels.png",
        output.getvalue(),
        "image/png",
    )

    assert response.status_code == 422
def test_media_reference_validation_delete_guard_and_same_invitation_scope(editor_context):
    client, _, ids, _ = editor_context
    _login(client, "owner@example.com")
    uploaded = _upload_personal(
        client,
        ids["personal_invitation"],
        "photo.png",
        _image_bytes("PNG"),
        "image/png",
    )
    assert uploaded.status_code == 201
    media_id = uploaded.json()["id"]
    sections_url = _personal_sections_url(ids["personal_invitation"])
    section = client.get(sections_url).json()[0]

    referenced = client.patch(
        f"{sections_url}/{section['id']}",
        headers=_csrf(client),
        json={"content": {"images": [{"media_id": media_id, "display_order": 1}]}},
    )
    assert referenced.status_code == 200
    blocked_delete = client.delete(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/media/{media_id}",
        headers=_csrf(client),
    )
    assert blocked_delete.status_code == 409

    workspace_sections = _workspace_sections_url(
        ids["workspace"], ids["workspace_invitation"]
    )
    _login(client, "editor@example.com")
    workspace_section = client.get(workspace_sections).json()[0]
    cross_invitation = client.patch(
        f"{workspace_sections}/{workspace_section['id']}",
        headers=_csrf(client),
        json={"content": {"media": [{"media_id": media_id}]}},
    )
    assert cross_invitation.status_code == 422


def test_media_delete_removes_unreferenced_file_and_workspace_member_cannot_mutate(
    editor_context,
):
    client, _, ids, storage = editor_context
    _login(client, "owner@example.com")
    upload = _upload_personal(
        client,
        ids["personal_invitation"],
        "photo.png",
        _image_bytes("PNG"),
        "image/png",
    )
    media_id = upload.json()["id"]
    file_url = upload.json()["file_url"]
    media_detail = client.get(
        f"/api/v1/invitations/personal/{ids['personal_invitation']}/media/{media_id}"
    )
    assert media_detail.status_code == 200

    deleted = client.delete(file_url.rsplit("/file", 1)[0], headers=_csrf(client))
    assert deleted.status_code == 204
    assert client.get(file_url).status_code == 404

    workspace_url = (
        f"/api/v1/invitations/workspaces/{ids['workspace']}/"
        f"{ids['workspace_invitation']}/media"
    )
    _login(client, "member@example.com")
    denied_upload = client.post(
        workspace_url,
        headers=_csrf(client),
        files={
            "upload": (
                "image.png",
                _image_bytes("PNG"),
                "image/png",
            )
        },
    )
    assert denied_upload.status_code == 403
    assert not list(storage.root.glob("*.png"))


def test_upload_database_failure_removes_written_file(editor_context, monkeypatch):
    _, session_factory, ids, storage = editor_context
    with session_factory() as session:
        user = session.get(User, ids["owner"])

        def fail_commit():
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated database failure"):
            MediaService(session, storage).upload_personal(
                user,
                ids["personal_invitation"],
                "photo.png",
                "image/png",
                _image_bytes("PNG"),
            )
        assert not list(storage.root.glob("*.png"))
        assert session.scalar(
            select(InvitationMedia.id).where(
                InvitationMedia.invitation_id == ids["personal_invitation"]
            )
        ) is None


def test_workspace_media_access_is_membership_scoped_and_unauthed(editor_context):
    client, _, ids, _ = editor_context
    workspace_url = (
        f"/api/v1/invitations/workspaces/{ids['workspace']}/"
        f"{ids['workspace_invitation']}/media"
    )
    assert client.get(workspace_url).status_code == 401

    _login(client, "outsider@example.com")
    assert client.get(workspace_url).status_code == 403
