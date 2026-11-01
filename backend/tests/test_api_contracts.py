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


def test_catalog_admin_openapi_documents_platform_admin_and_csrf_contract():
    schema = app.openapi()
    paths = schema["paths"]
    plans = paths["/api/v1/admin/plans"]
    features = paths["/api/v1/admin/features"]
    create_plan = plans["post"]
    update_plan = paths["/api/v1/admin/plans/{plan_id}"]["patch"]
    map_feature = paths["/api/v1/admin/plans/{plan_id}/features"]["post"]

    assert create_plan["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert plans["get"]["security"] == [{"AccessTokenCookie": []}]
    assert update_plan["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert create_plan["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/PlanCreate"
    assert create_plan["responses"]["201"]
    assert create_plan["responses"]["401"]
    assert create_plan["responses"]["403"]
    assert create_plan["responses"]["409"]
    assert create_plan["responses"]["422"]
    assert map_feature["responses"]["404"]
    assert map_feature["responses"]["409"]
    assert features["post"]["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert "price" in schema["components"]["schemas"]["PlanRead"]["properties"]
    assert "updated_at" in schema["components"]["schemas"]["FeatureRead"]["properties"]
    for path, path_item in paths.items():
        if not path.startswith(("/api/v1/admin/plans", "/api/v1/admin/features")):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "patch", "delete"}:
                continue
            expected = {"AccessTokenCookie": []}
            if method in {"post", "patch", "delete"}:
                expected["CSRFToken"] = []
            assert operation["security"] == [expected]


def test_billing_openapi_documents_authenticated_read_and_csrf_creation():
    schema = app.openapi()
    paths = schema["paths"]

    purchase_create = paths["/api/v1/purchases"]["post"]
    subscription_create = paths["/api/v1/subscriptions"]["post"]
    payment_read = paths["/api/v1/payments/{payment_id}"]["get"]
    assert purchase_create["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert subscription_create["security"] == [
        {"AccessTokenCookie": [], "CSRFToken": []}
    ]
    assert paths["/api/v1/purchases/{purchase_id}"]["get"]["security"] == [
        {"AccessTokenCookie": []}
    ]
    assert payment_read["security"] == [{"AccessTokenCookie": []}]
    assert purchase_create["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/PersonalPurchaseCreate"
    assert "201" in purchase_create["responses"]
    assert "409" in subscription_create["responses"]
    assert "PaymentRead" in schema["components"]["schemas"]
    assert "entitlement_snapshot" not in schema["components"]["schemas"][
        "PersonalPurchaseRead"
    ]["properties"]
    assert "provider_reference" not in schema["components"]["schemas"][
        "PaymentRead"
    ]["properties"]
    assert set(paths["/api/v1/payments/{payment_id}"]) == {"get"}


def test_openapi_has_no_duplicate_method_path_routes():
    registered = set()

    def operations(router, prefix=""):
        for route in router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    yield method, f"{prefix}{route.path}"
                continue
            included_router = getattr(route, "original_router", None)
            include_context = getattr(route, "include_context", None)
            if included_router is not None and include_context is not None:
                yield from operations(
                    included_router,
                    f"{prefix}{include_context.prefix}",
                )

    for method, path in operations(app):
        key = (method, path)
        assert key not in registered, f"Duplicate route: {method} {path}"
        registered.add(key)
