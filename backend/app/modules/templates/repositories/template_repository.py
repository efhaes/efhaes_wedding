from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.templates.models.template import Template


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_slug(self, slug: str, *, public_only: bool = False) -> Template | None:
        statement = select(Template).where(Template.slug == slug)
        if public_only:
            statement = statement.where(
                Template.status == "published",
                Template.is_active.is_(True),
            )
        return self.session.scalar(statement)

    def find_by_id(self, template_id: UUID) -> Template | None:
        return self.session.get(Template, template_id)

    def slug_exists(self, slug: str, *, exclude_id: UUID | None = None) -> bool:
        statement = select(Template.id).where(Template.slug == slug)
        if exclude_id is not None:
            statement = statement.where(Template.id != exclude_id)
        return self.session.scalar(statement.limit(1)) is not None

    def list_public(
        self,
        *,
        category: str | None,
        search: str | None,
        page: int,
        page_size: int,
        sort_by: Literal["name", "created_at"],
        sort_order: Literal["asc", "desc"],
    ) -> tuple[list[Template], int]:
        statement = select(Template).where(
            Template.status == "published",
            Template.is_active.is_(True),
        )
        if category:
            statement = statement.where(Template.category == category)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Template.name.ilike(f"%{escaped}%", escape="\\"))

        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = self.session.scalar(count_statement) or 0
        sort_column = Template.name if sort_by == "name" else Template.created_at
        sort_column = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        statement = statement.order_by(sort_column, Template.id).offset(
            (page - 1) * page_size
        ).limit(page_size)
        return list(self.session.scalars(statement)), total

    def list_categories(self) -> list[str]:
        return list(
            self.session.scalars(
                select(Template.category)
                .where(
                    Template.status == "published",
                    Template.is_active.is_(True),
                )
                .distinct()
                .order_by(Template.category)
            )
        )

    def list_admin(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str | None,
    ) -> tuple[list[Template], int]:
        statement = select(Template)
        if status_filter:
            statement = statement.where(Template.status == status_filter)
        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        statement = (
            statement.order_by(Template.created_at.desc(), Template.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement)), total

    def list_all(self) -> list[Template]:
        return list(self.session.scalars(select(Template)))

    def add(self, template: Template) -> Template:
        self.session.add(template)
        return template
