from fastapi.routing import APIRoute

from app.main import app


def test_template_openapi_contract_documents_schemas_and_admin_security():
    schema = app.openapi()
    paths = schema["paths"]

    assert "TemplateCatalogPage" in schema["components"]["schemas"]
    assert "TemplateCatalogRead" in schema["components"]["schemas"]
    assert "TemplatePreview" in schema["components"]["schemas"]
    assert "TemplateAdminPage" in schema["components"]["schemas"]

    catalog_fields = schema["components"]["schemas"]["TemplateCatalogRead"][
        "properties"
    ]
    preview_fields = schema["components"]["schemas"]["TemplatePreview"][
        "properties"
    ]
    assert not {"status", "is_active", "version", "updated_at", "configuration"} & (
        catalog_fields.keys()
    )
    assert not {"status", "is_active", "version", "updated_at"} & preview_fields.keys()
    assert {"design_config", "default_sections"} <= preview_fields.keys()

    assert paths["/api/v1/templates/"]["get"]["responses"]["429"]
    assert paths["/api/v1/templates/{slug}/preview"]["get"]["responses"]["404"]
    assert paths["/api/v1/templates/{slug}/preview"]["get"]["responses"]["409"]
    assert paths["/api/v1/templates/admin"]["get"]["security"] == [
        {"AccessTokenCookie": []}
    ]
    assert paths["/api/v1/templates/admin"]["post"]["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert paths["/api/v1/templates/{slug}/preview"]["get"]["responses"]["409"][
        "content"
    ]["application/json"]["schema"]["$ref"] == "#/components/schemas/APIError"


def test_public_invitation_openapi_documents_optional_bearer_and_no_token_query():
    schema = app.openapi()
    paths = schema["paths"]
    guest_detail = paths["/api/v1/public/invitations/{slug}"]["get"]
    rsvp = paths["/api/v1/public/invitations/{slug}/rsvp"]["post"]
    guestbook = paths["/api/v1/public/invitations/{slug}/guestbook"]["post"]

    assert {"GuestBearer": []} in guest_detail["security"]
    assert {} in guest_detail["security"]
    assert rsvp["security"] == [{"GuestBearer": []}]
    assert {"GuestBearer": []} in guestbook["security"]

    for operation in (guest_detail, rsvp, guestbook):
        assert "guest_token" not in {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter["in"] == "query"
        }

    assert "Authorization: Bearer" in guest_detail["description"]
    assert "query-string tokens" in rsvp["description"]
    assert rsvp["responses"]["401"]
    assert rsvp["responses"]["409"]
    assert rsvp["responses"]["429"]
    assert guestbook["responses"]["403"]
    assert guestbook["responses"]["404"]
    assert (
        rsvp["responses"]["401"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/APIError"
    )

    assert paths["/api/v1/public/invitations/{slug}/music"]["get"].get(
        "security"
    ) is None

    assert "storage_key" not in schema["components"]["schemas"]["PublicMedia"][
        "properties"
    ]
    assert "invitation_token" not in schema["components"]["schemas"][
        "GuestRead"
    ]["properties"]
    public_section = schema["components"]["schemas"]["PublicSection"]["properties"]
    assert (
        "allow-listed public content"
        in public_section["content"]["description"].lower()
    )


def test_openapi_has_no_duplicate_method_path_routes():
    registered = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, route.path)
            assert key not in registered, f"Duplicate route: {method} {route.path}"
            registered.add(key)
