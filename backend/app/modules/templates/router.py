from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_csrf, require_platform_admin
from app.core.rate_limit import rate_limit
from app.modules.templates.schemas.template import (
    TemplateAdminCreate,
    TemplateAdminPage,
    TemplateAdminRead,
    TemplateAdminUpdate,
    TemplateCatalogPage,
    TemplateCatalogRead,
    TemplatePreview,
)
from app.modules.templates.services.template_service import TemplateService
from app.modules.users.models.user import User


router = APIRouter(prefix="/templates", tags=["templates"])
AdminAccess = Annotated[User, Depends(require_platform_admin)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/",
    response_model=TemplateCatalogPage,
    summary="List published templates",
    description=(
        "Returns active published templates only. Supports category and text "
        "filters, stable sorting, and page/page_size pagination."
    ),
    responses={429: {"description": "Request rate limit exceeded"}},
    dependencies=[Depends(rate_limit("public"))],
)
def list_templates(
    session: Session = Depends(get_db),
    category: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    sort_by: Literal["name", "created_at"] = "name",
    sort_order: Literal["asc", "desc"] = "asc",
) -> TemplateCatalogPage:
    return TemplateService(session).list_public(
        category=category,
        search=search,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/categories",
    response_model=list[str],
    summary="List published template categories",
    responses={429: {"description": "Request rate limit exceeded"}},
    dependencies=[Depends(rate_limit("public"))],
)
def list_categories(session: Session = Depends(get_db)) -> list[str]:
    return TemplateService(session).categories()


@router.get(
    "/admin",
    response_model=TemplateAdminPage,
    summary="List templates for platform administration",
    description="Requires a platform administrator session.",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        409: {"description": "Stored template configuration is invalid"},
    },
)
def list_admin_templates(
    _admin: AdminAccess,
    session: Session = Depends(get_db),
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status_filter: Literal["draft", "published", "archived"] | None = Query(
        default=None, alias="status"
    ),
) -> dict:
    return TemplateService(session).list_admin(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
    )


@router.post(
    "/admin",
    response_model=TemplateAdminRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="Create a draft template",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        409: {"description": "Template slug already exists"},
    },
)
def create_template(
    payload: TemplateAdminCreate,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).create(payload)


@router.get(
    "/admin/{template_id}",
    response_model=TemplateAdminRead,
    summary="Get a template for platform administration",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Template not found"},
        409: {"description": "Stored template configuration is invalid"},
    },
)
def get_admin_template(
    template_id: UUID,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).get_admin(template_id)


@router.patch(
    "/admin/{template_id}",
    response_model=TemplateAdminRead,
    dependencies=[Depends(require_csrf)],
    summary="Update a template",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Template not found"},
        409: {"description": "Template conflict or invalid stored configuration"},
    },
)
def update_template(
    template_id: UUID,
    payload: TemplateAdminUpdate,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).update(template_id, payload)


@router.post(
    "/admin/{template_id}/publish",
    response_model=TemplateAdminRead,
    dependencies=[Depends(require_csrf)],
    summary="Publish a template",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Template not found"},
        409: {"description": "Template cannot be published"},
    },
)
def publish_template(
    template_id: UUID,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).publish(template_id)


@router.post(
    "/admin/{template_id}/archive",
    response_model=TemplateAdminRead,
    dependencies=[Depends(require_csrf)],
    summary="Archive a template",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Template not found"},
    },
)
def archive_template(
    template_id: UUID,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).archive(template_id)


@router.post(
    "/admin/{template_id}/draft",
    response_model=TemplateAdminRead,
    dependencies=[Depends(require_csrf)],
    summary="Restore an archived template to draft",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator role required"},
        404: {"description": "Template not found"},
        409: {"description": "Only archived templates can be restored"},
    },
)
def restore_template_to_draft(
    template_id: UUID,
    _admin: AdminAccess,
    session: Session = Depends(get_db),
) -> dict:
    return TemplateService(session).restore_to_draft(template_id)


@router.get(
    "/{slug}/preview",
    response_model=TemplatePreview,
    summary="Get published template preview configuration",
    description="Returns validated public design configuration without admin fields.",
    responses={
        404: {"description": "Published template not found"},
        409: {"description": "Stored template configuration is invalid"},
        429: {"description": "Request rate limit exceeded"},
    },
    dependencies=[Depends(rate_limit("public"))],
)
def preview_template(
    slug: str,
    session: Session = Depends(get_db),
) -> TemplatePreview:
    return TemplateService(session).preview(slug)


@router.get(
    "/{slug}",
    response_model=TemplateCatalogRead,
    summary="Get a published template",
    responses={
        404: {"description": "Published template not found"},
        429: {"description": "Request rate limit exceeded"},
    },
    dependencies=[Depends(rate_limit("public"))],
)
def get_template(
    slug: str,
    session: Session = Depends(get_db),
) -> TemplateCatalogRead:
    return TemplateService(session).get_public_by_slug(slug)
