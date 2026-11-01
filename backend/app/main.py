from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.config import settings
from app.core.middleware import (
    LegacyGuestTokenQueryMiddleware,
    RequestBodyLimitMiddleware,
)
from app.modules.auth.router import router as auth_router
from app.modules.guestbook.router import router as guestbook_router
from app.modules.invitation_editor.router import router as invitation_editor_router
from app.modules.invitations.router import router as invitations_router
from app.modules.invitation_music.router import router as invitation_music_router
from app.modules.media.router import router as media_router
from app.modules.public_invitations.router import router as public_invitations_router
from app.modules.rsvp.router import router as rsvp_router
from app.modules.users.router import router as users_router
from app.modules.templates.router import router as templates_router
from app.modules.workspaces.router import router as workspaces_router
from app.modules.entitlements.routers.catalog_admin_router import (
    router as catalog_admin_router,
)
from app.modules.billing.routers.billing_router import router as billing_router


app = FastAPI(title="Efhaess Wedding API")
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(LegacyGuestTokenQueryMiddleware)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
app.include_router(templates_router, prefix="/api/v1")
app.include_router(catalog_admin_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(invitations_router, prefix="/api/v1")
app.include_router(invitation_editor_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")
app.include_router(public_invitations_router, prefix="/api/v1")
app.include_router(rsvp_router)
app.include_router(invitation_music_router)
app.include_router(guestbook_router)


def custom_openapi() -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
    )
    security_schemes = schema.setdefault("components", {}).setdefault(
        "securitySchemes", {}
    )
    security_schemes.update(
        {
            "AccessTokenCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": settings.ACCESS_COOKIE_NAME,
                "description": "HttpOnly access-token cookie issued by the auth API.",
            },
            "CSRFToken": {
                "type": "apiKey",
                "in": "header",
                "name": settings.CSRF_HEADER_NAME,
                "description": (
                    "Required for state-changing requests; the value must match "
                    f"the `{settings.CSRF_COOKIE_NAME}` cookie issued by "
                    "`GET /api/v1/auth/csrf`."
                ),
            },
            "GuestBearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "Guest token",
                "description": (
                    "Optional personalized guest token. Send as "
                    "`Authorization: Bearer <guest-token>`; never put it in a query."
                ),
            },
        }
    )
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["APIError"] = {
        "title": "APIError",
        "type": "object",
        "required": ["detail"],
        "properties": {
            "detail": {
                "title": "Detail",
                "type": "string",
                "description": "Human-readable error message.",
            }
        },
    }

    for path, path_item in schema.get("paths", {}).items():
        is_template_admin = path.startswith("/api/v1/templates/admin")
        is_catalog_admin = path.startswith(
            ("/api/v1/admin/plans", "/api/v1/admin/features")
        )
        is_billing = path.startswith(
            ("/api/v1/purchases", "/api/v1/subscriptions", "/api/v1/payments")
        )
        is_invitation_management = path.startswith(
            ("/api/v1/invitations/personal/", "/api/v1/invitations/workspaces/")
        )
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            is_template_or_public_invitation = path.startswith(
                ("/api/v1/templates/", "/api/v1/public/invitations/")
            )
            if is_template_or_public_invitation or is_catalog_admin or is_billing:
                for response_code in (
                    "401",
                    "403",
                    "404",
                    "409",
                    "413",
                    "415",
                    "416",
                    "429",
                ):
                    response = operation.get("responses", {}).get(response_code)
                    if response is not None and "content" not in response:
                        response["content"] = {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/APIError"}
                            }
                        }
            if (
                is_template_admin
                or is_catalog_admin
                or is_invitation_management
                or is_billing
            ):
                schemes = {"AccessTokenCookie": []}
                if method in {"post", "put", "patch", "delete"}:
                    schemes["CSRFToken"] = []
                operation["security"] = [schemes]

    optional_guest_auth = {
        ("/api/v1/public/invitations/{slug}", "get"),
        ("/api/v1/public/invitations/{slug}/guestbook", "post"),
    }
    for path, method in optional_guest_auth:
        operation = schema["paths"].get(path, {}).get(method)
        if operation is not None:
            operation["security"] = [{}, {"GuestBearer": []}]
    rsvp_operation = schema["paths"].get(
        "/api/v1/public/invitations/{slug}/rsvp", {}
    ).get("post")
    if rsvp_operation is not None:
        rsvp_operation["security"] = [{"GuestBearer": []}]

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi