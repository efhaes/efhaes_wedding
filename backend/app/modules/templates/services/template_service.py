from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.modules.templates.models.template import Template
from app.modules.templates.repositories.template_repository import TemplateRepository
from app.modules.templates.schemas.template import (
    DefaultSection,
    TemplateAdminCreate,
    TemplateAdminUpdate,
    TemplateCatalogPage,
    TemplateCatalogRead,
    TemplatePreview,
    validate_design_config,
)


class TemplateService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = TemplateRepository(session)

    def list_public(
        self,
        *,
        category: str | None,
        search: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["name", "created_at"],
        sort_order: Literal["asc", "desc"],
    ) -> TemplateCatalogPage:
        templates, total = self.repository.list_public(
            category=category.strip() if category else None,
            search=search.strip() if search else None,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return TemplateCatalogPage(
            items=[self.catalog_read(template) for template in templates],
            page=page,
            page_size=page_size,
            total=total,
        )

    def categories(self) -> list[str]:
        return self.repository.list_categories()

    def get_public_by_slug(self, slug: str) -> TemplateCatalogRead:
        template = self.repository.find_by_slug(slug, public_only=True)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return self.catalog_read(template)

    def preview(self, slug: str) -> TemplatePreview:
        template = self.repository.find_by_slug(slug, public_only=True)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return self.preview_read(template)

    def list_admin(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str | None,
    ) -> dict[str, Any]:
        templates, total = self.repository.list_admin(
            page=page,
            page_size=page_size,
            status_filter=status_filter,
        )
        return {
            "items": [self.admin_read(template) for template in templates],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def get_admin(self, template_id: UUID) -> dict[str, Any]:
        template = self.repository.find_by_id(template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return self.admin_read(template)

    def create(self, payload: TemplateAdminCreate) -> dict[str, Any]:
        if self.repository.slug_exists(payload.slug):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Template slug already exists")
        template = Template(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            category=payload.category,
            access_level=payload.access_level,
            thumbnail_url=str(payload.thumbnail_url) if payload.thumbnail_url else None,
            preview_image_url=(
                str(payload.preview_image_url) if payload.preview_image_url else None
            ),
            configuration=self._configuration(
                payload.design_config, payload.default_sections
            ),
            status="draft",
            is_active=True,
        )
        self.repository.add(template)
        self._commit()
        return self.admin_read(template)

    def update(self, template_id: UUID, payload: TemplateAdminUpdate) -> dict[str, Any]:
        template = self.repository.find_by_id(template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

        changes = payload.model_dump(exclude_unset=True)
        self._validate_stored_configuration(template)
        if "slug" in changes and self.repository.slug_exists(
            changes["slug"], exclude_id=template.id
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Template slug already exists")

        for field in ("name", "slug", "description", "category", "access_level"):
            if field in changes:
                setattr(template, field, changes[field])
        for field in ("thumbnail_url", "preview_image_url"):
            if field in changes:
                value = changes[field]
                setattr(template, field, str(value) if value else None)

        if "design_config" in changes or "default_sections" in changes:
            current_design, current_sections = self._configuration_parts(template.configuration)
            design = changes.get("design_config", current_design)
            sections = changes.get("default_sections", current_sections)
            try:
                template.configuration = self._configuration(design, sections)
            except (ValidationError, TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Stored template configuration is invalid",
                ) from error
        template.version += 1
        self._commit()
        return self.admin_read(template)

    def publish(self, template_id: UUID) -> dict[str, Any]:
        template = self._get_for_status_change(template_id)
        if template.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Restore the archived template to draft before publishing",
            )
        self._validate_stored_configuration(template)
        if template.status != "published":
            template.status = "published"
            template.is_active = True
            self._commit()
        return self.admin_read(template)

    def archive(self, template_id: UUID) -> dict[str, Any]:
        template = self._get_for_status_change(template_id)
        self._validate_stored_configuration(template)
        if template.status != "archived":
            template.status = "archived"
            template.is_active = False
            self._commit()
        return self.admin_read(template)

    def restore_to_draft(self, template_id: UUID) -> dict[str, Any]:
        template = self._get_for_status_change(template_id)
        self._validate_stored_configuration(template)
        if template.status != "archived":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only archived templates can be restored to draft",
            )
        template.status = "draft"
        template.is_active = True
        self._commit()
        return self.admin_read(template)

    def _get_for_status_change(self, template_id: UUID) -> Template:
        template = self.repository.find_by_id(template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return template

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if constraint == "templates_slug_key" or "templates.slug" in str(error.orig).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Template slug already exists",
                ) from error
            raise

    @staticmethod
    def _configuration(
        design: dict[str, Any],
        sections: list[DefaultSection] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        validated_design = validate_design_config(design)
        validated_sections = [
            section
            if isinstance(section, DefaultSection)
            else DefaultSection.model_validate(section)
            for section in sections
        ]
        keys = [section.key for section in validated_sections]
        if len(keys) != len(set(keys)):
            raise ValueError("Default section keys must be unique")
        return {
            "design": validated_design,
            "default_sections": [
                section.model_dump(mode="json") for section in validated_sections
            ],
        }

    @classmethod
    def _validate_stored_configuration(cls, template: Template) -> None:
        cls._validated_configuration_parts(template)

    @staticmethod
    def _configuration_parts(
        configuration: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not isinstance(configuration, dict):
            return {}, []
        design = configuration.get("design")
        sections = configuration.get("default_sections")
        if isinstance(design, dict):
            return design, sections if isinstance(sections, list) else []
        return configuration, []

    @classmethod
    def catalog_read(cls, template: Template) -> TemplateCatalogRead:
        return TemplateCatalogRead(
            id=template.id,
            name=template.name,
            slug=template.slug,
            description=template.description,
            category=template.category,
            access_level=template.access_level,
            thumbnail_url=template.thumbnail_url,
            preview_image_url=template.preview_image_url,
            created_at=template.created_at,
        )

    @classmethod
    def admin_read(cls, template: Template) -> dict[str, Any]:
        design, sections = cls._validated_configuration_parts(template)
        return {
            **cls.catalog_read(template).model_dump(),
            "status": template.status,
            "is_active": template.is_active,
            "version": template.version,
            "updated_at": template.updated_at,
            "design_config": design,
            "default_sections": [
                section.model_dump(mode="json") for section in sections
            ],
        }

    @classmethod
    def preview_read(cls, template: Template) -> TemplatePreview:
        design, sections = cls._validated_configuration_parts(template)
        return TemplatePreview(
            **cls.catalog_read(template).model_dump(),
            design_config=design,
            default_sections=sections,
        )

    @classmethod
    def _validated_configuration_parts(
        cls,
        template: Template,
    ) -> tuple[dict[str, Any], list[DefaultSection]]:
        configuration = template.configuration
        if not isinstance(configuration, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored template configuration is invalid",
            )
        if "design" in configuration:
            if set(configuration) - {"design", "default_sections"}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Stored template configuration is invalid",
                )
            design = configuration["design"]
            sections = configuration.get("default_sections", [])
        else:
            design = configuration
            sections = []
        if not isinstance(design, dict) or not isinstance(sections, list):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored template configuration is invalid",
            )
        try:
            validated_design = validate_design_config(design)
            validated_sections = [
                DefaultSection.model_validate(section) for section in sections
            ]
            keys = [section.key for section in validated_sections]
            if len(keys) != len(set(keys)):
                raise ValueError("Default section keys must be unique")
        except (ValidationError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored template configuration is invalid",
            ) from error
        return validated_design, validated_sections
