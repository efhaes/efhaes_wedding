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
from app.modules.auth.models.refresh_token import RefreshToken
from app.models.rate_limit_bucket import RateLimitBucket
from app.modules.templates.models.template import Template
from app.modules.users.models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def template_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        RefreshToken.__table__,
        Template.__table__,
        RateLimitBucket.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        admin = User(
            name="Platform Admin",
            email="platform@example.com",
            password_hash=hash_password("platform-password-123"),
            platform_role="admin",
        )
        regular_user = User(
            name="Regular User",
            email="regular@example.com",
            password_hash=hash_password("regular-password-123"),
        )
        workspace_admin = User(
            name="Workspace Admin",
            email="workspace-admin@example.com",
            password_hash=hash_password("workspace-password-123"),
        )
        session.add_all([admin, regular_user, workspace_admin])
        session.commit()

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
    client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers=_csrf(client),
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def _template_payload(
    slug: str,
    *,
    name: str = "Garden Wedding",
    category: str = "garden",
) -> dict:
    return {
        "name": name,
        "slug": slug,
        "description": "A light garden theme",
        "category": category,
        "access_level": "free",
        "thumbnail_url": "https://cdn.example.com/thumb.jpg",
        "preview_image_url": "https://cdn.example.com/preview.jpg",
        "design_config": {"colors": {"primary": "#123456"}},
        "default_sections": [
            {
                "key": "main_cover",
                "type": "cover",
                "title": "Welcome",
                "is_enabled": True,
                "configuration": {"layout": "centered"},
            },
            {
                "key": "event_details",
                "type": "event",
                "title": "Event Details",
            },
        ],
    }


def _insert_template(
    session_factory,
    slug: str,
    *,
    status: str,
    name: str,
    category: str,
    created_order: int,
) -> UUID:
    from datetime import datetime, timedelta, timezone

    with session_factory() as session:
        template = Template(
            name=name,
            slug=slug,
            category=category,
            access_level="free",
            description="Fixture template",
            thumbnail_url="https://cdn.example.com/thumb.jpg",
            preview_image_url="https://cdn.example.com/preview.jpg",
            configuration={
                "design": {"palette": {"primary": "#abcabc"}},
                "default_sections": [
                    {"key": "cover", "type": "cover", "title": "Cover"}
                ],
            },
            status=status,
            is_active=status != "archived",
        )
        session.add(template)
        session.flush()
        template.created_at = datetime.now(timezone.utc) + timedelta(minutes=created_order)
        session.commit()
        return template.id


def test_public_catalog_filters_searches_paginates_and_excludes_nonpublished(template_context):
    client, session_factory = template_context
    published_id = _insert_template(
        session_factory, "garden-one", status="published", name="Garden One", category="garden", created_order=1
    )
    _insert_template(
        session_factory, "garden-two", status="published", name="Garden Two", category="garden", created_order=2
    )
    _insert_template(
        session_factory, "beach-one", status="published", name="Beach One", category="beach", created_order=3
    )
    _insert_template(
        session_factory, "draft-one", status="draft", name="Garden Draft", category="garden", created_order=4
    )
    _insert_template(
        session_factory, "archived-one", status="archived", name="Garden Archived", category="garden", created_order=5
    )

    catalog = client.get("/api/v1/templates/")
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 3
    assert {item["slug"] for item in catalog.json()["items"]} == {
        "garden-one",
        "garden-two",
        "beach-one",
    }

    category = client.get("/api/v1/templates/?category=garden")
    assert category.json()["total"] == 2
    search = client.get("/api/v1/templates/?search=Garden")
    assert search.json()["total"] == 2
    page = client.get("/api/v1/templates/?page=2&page_size=1&sort_by=name")
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1
    assert page.json()["page"] == 2
    assert len(client.get("/api/v1/templates/categories").json()) == 2

    with session_factory() as session:
        assert session.get(Template, published_id).status == "published"


def test_public_detail_preview_and_unpublished_template_404(template_context):
    client, session_factory = template_context
    _insert_template(
        session_factory, "published-detail", status="published", name="Detail", category="classic", created_order=1
    )
    _insert_template(
        session_factory, "draft-detail", status="draft", name="Draft", category="classic", created_order=2
    )
    detail = client.get("/api/v1/templates/published-detail")
    assert detail.status_code == 200
    assert detail.json()["preview_image_url"] == "https://cdn.example.com/preview.jpg"

    preview = client.get("/api/v1/templates/published-detail/preview")
    assert preview.status_code == 200
    assert preview.json()["design_config"]["palette"]["primary"] == "#abcabc"
    assert preview.json()["default_sections"][0]["key"] == "cover"

    assert client.get("/api/v1/templates/draft-detail").status_code == 404
    assert client.get("/api/v1/templates/draft-detail/preview").status_code == 404
    assert client.get("/api/v1/templates/not-found").status_code == 404


def test_public_preview_excludes_admin_fields_and_validates_stored_configuration(
    template_context,
):
    client, session_factory = template_context
    template_id = _insert_template(
        session_factory,
        "safe-preview",
        status="published",
        name="Safe Preview",
        category="classic",
        created_order=1,
    )

    preview = client.get("/api/v1/templates/safe-preview/preview")
    assert preview.status_code == 200
    assert preview.json()["default_sections"][0]["key"] == "cover"
    assert not {
        "status",
        "is_active",
        "version",
        "updated_at",
    }.intersection(preview.json())

    with session_factory() as session:
        template = session.get(Template, template_id)
        template.configuration = {
            "design": {"palette": "classic"},
            "default_sections": [],
            "internal_note": "not for clients",
        }
        session.commit()

    assert client.get("/api/v1/templates/safe-preview/preview").status_code == 409
    assert client.get(
        f"/api/v1/templates/admin/{template_id}",
    ).status_code == 401
    _login(client, "platform@example.com", "platform-password-123")
    assert client.get(
        f"/api/v1/templates/admin/{template_id}",
    ).status_code == 409
    assert client.post(
        f"/api/v1/templates/admin/{template_id}/archive",
        headers=_csrf(client),
    ).status_code == 409
    with session_factory() as session:
        template = session.get(Template, template_id)
        assert template.status == "published"
        template.status = "archived"
        template.is_active = False
        session.commit()
    assert client.post(
        f"/api/v1/templates/admin/{template_id}/draft",
        headers=_csrf(client),
    ).status_code == 409
    with session_factory() as session:
        assert session.get(Template, template_id).status == "archived"


def test_inactive_published_template_is_hidden_from_public_catalog(template_context):
    client, session_factory = template_context
    template_id = _insert_template(
        session_factory,
        "inactive-template",
        status="published",
        name="Inactive Template",
        category="classic",
        created_order=1,
    )
    with session_factory() as session:
        session.get(Template, template_id).is_active = False
        session.commit()

    catalog = client.get("/api/v1/templates/")
    assert catalog.status_code == 200
    assert "inactive-template" not in {
        item["slug"] for item in catalog.json()["items"]
    }
    assert client.get("/api/v1/templates/inactive-template").status_code == 404
    assert (
        client.get("/api/v1/templates/inactive-template/preview").status_code
        == 404
    )


def test_public_template_endpoints_are_rate_limited(template_context, monkeypatch):
    from app.core.config import settings

    client, session_factory = template_context
    _insert_template(
        session_factory,
        "rate-limited-template",
        status="published",
        name="Rate Limited",
        category="classic",
        created_order=1,
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_REQUESTS", 1)

    assert client.get("/api/v1/templates/categories").status_code == 200
    limited = client.get("/api/v1/templates/")
    assert limited.status_code == 429
    assert limited.json() == {
        "detail": "Too many requests. Please try again later."
    }


def test_platform_admin_template_lifecycle_and_slug_uniqueness(template_context):
    client, session_factory = template_context
    _login(client, "platform@example.com", "platform-password-123")

    created = client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=_template_payload("admin-garden"),
    )
    assert created.status_code == 201
    result = created.json()
    template_id = UUID(result["id"])
    assert result["status"] == "draft"
    assert result["default_sections"][0]["key"] == "main_cover"

    duplicate = client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=_template_payload("admin-garden", name="Duplicate"),
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/api/v1/templates/admin/{template_id}",
        headers=_csrf(client),
        json={"name": "Updated Garden", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Garden"
    assert updated.json()["description"] is None

    admin_list = client.get("/api/v1/templates/admin")
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 1
    assert client.get(f"/api/v1/templates/admin/{template_id}").status_code == 200

    publish = client.post(
        f"/api/v1/templates/admin/{template_id}/publish",
        headers=_csrf(client),
    )
    assert publish.status_code == 200
    assert publish.json()["status"] == "published"
    assert client.get("/api/v1/templates/admin-garden").status_code == 200

    archive = client.post(
        f"/api/v1/templates/admin/{template_id}/archive",
        headers=_csrf(client),
    )
    assert archive.status_code == 200
    assert archive.json()["status"] == "archived"
    assert client.get("/api/v1/templates/admin-garden").status_code == 404

    restore = client.post(
        f"/api/v1/templates/admin/{template_id}/draft",
        headers=_csrf(client),
    )
    assert restore.status_code == 200
    assert restore.json()["status"] == "draft"
    assert client.get("/api/v1/templates/").json()["total"] == 0

    with session_factory() as session:
        stored = session.get(Template, template_id)
        assert stored.status == "draft"
        assert stored.version == 2


def test_regular_and_workspace_admin_cannot_manage_templates(template_context):
    client, _ = template_context
    _login(client, "regular@example.com", "regular-password-123")
    assert client.get("/api/v1/templates/admin").status_code == 403
    assert client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=_template_payload("regular-create"),
    ).status_code == 403

    workspace_admin = TestClient(app)
    _login(
        workspace_admin,
        "workspace-admin@example.com",
        "workspace-password-123",
    )
    assert workspace_admin.get("/api/v1/templates/admin").status_code == 403


def test_template_validation_and_public_pagination_bounds(template_context):
    client, _ = template_context
    _login(client, "platform@example.com", "platform-password-123")
    invalid = _template_payload("Bad Slug")
    assert client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=invalid,
    ).status_code == 422

    invalid_sections = _template_payload("valid-slug")
    invalid_sections["default_sections"] = [
        {"key": "cover", "type": "cover", "title": "Cover"},
        {"key": "cover", "type": "event", "title": "Event"},
    ]
    assert client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=invalid_sections,
    ).status_code == 422
    assert client.get("/api/v1/templates/?page_size=101").status_code == 422


def test_template_configuration_rejects_unknown_fields_and_invalid_publish_data(
    template_context,
):
    client, session_factory = template_context
    _login(client, "platform@example.com", "platform-password-123")

    invalid_design = _template_payload("invalid-design")
    invalid_design["design_config"] = {
        "colors": {"primary": "#123456", "background": "#ffffff"}
    }
    assert client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=invalid_design,
    ).status_code == 422

    invalid_section = _template_payload("invalid-rsvp")
    invalid_section["default_sections"] = [
        {
            "key": "rsvp",
            "type": "rsvp",
            "title": "RSVP",
            "configuration": {"allow_response_changes": "sometimes"},
        }
    ]
    assert client.post(
        "/api/v1/templates/admin",
        headers=_csrf(client),
        json=invalid_section,
    ).status_code == 422

    template_id = _insert_template(
        session_factory,
        "corrupt-template",
        status="draft",
        name="Corrupt Template",
        category="garden",
        created_order=50,
    )
    with session_factory() as session:
        template = session.get(Template, template_id)
        template.configuration = {
            "design": {"colors": {"primary": "javascript:alert(1)"}},
            "default_sections": [],
        }
        session.commit()

    response = client.post(
        f"/api/v1/templates/admin/{template_id}/publish",
        headers=_csrf(client),
    )
    assert response.status_code == 409
