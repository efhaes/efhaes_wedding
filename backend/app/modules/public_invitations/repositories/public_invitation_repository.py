from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.invitation_media import InvitationMedia
from app.models.invitation_section import InvitationSection
from app.models.invitation_view import InvitationView
from app.modules.invitations.models.invitation import Invitation
from app.modules.templates.models.template import Template


class PublicInvitationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_published(self, slug: str) -> Invitation | None:
        return self.session.scalar(
            select(Invitation).where(
                Invitation.slug == slug,
                Invitation.status == "published",
                (Invitation.expires_at.is_(None)) | (Invitation.expires_at > func.now()),
            )
        )

    def template_for(self, template_id: UUID) -> Template | None:
        return self.session.get(Template, template_id)

    def enabled_sections(self, invitation_id: UUID) -> list[InvitationSection]:
        return list(
            self.session.scalars(
                select(InvitationSection)
                .where(
                    InvitationSection.invitation_id == invitation_id,
                    InvitationSection.is_enabled.is_(True),
                )
                .order_by(InvitationSection.sort_order, InvitationSection.id)
            )
        )

    def sections_for_invitation(self, invitation_id: UUID) -> list[InvitationSection]:
        return list(
            self.session.scalars(
                select(InvitationSection)
                .where(InvitationSection.invitation_id == invitation_id)
                .order_by(InvitationSection.sort_order, InvitationSection.id)
            )
        )

    def media_for_ids(
        self,
        invitation_id: UUID,
        media_ids: set[UUID],
    ) -> dict[UUID, InvitationMedia]:
        if not media_ids:
            return {}
        rows = self.session.scalars(
            select(InvitationMedia).where(
                InvitationMedia.invitation_id == invitation_id,
                InvitationMedia.id.in_(media_ids),
                InvitationMedia.media_type == "image",
            )
        )
        return {media.id: media for media in rows}

    def find_guest_by_token(self, invitation_id: UUID, token_hash: str) -> Guest | None:
        return self.session.scalar(
            select(Guest).where(
                Guest.invitation_id == invitation_id,
                Guest.invitation_token == token_hash,
            )
        )

    def record_view(
        self,
        invitation_id: UUID,
        visitor_key_hash: str,
        guest_id: UUID | None,
    ) -> None:
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert = postgresql_insert
        elif dialect_name == "sqlite":
            insert = sqlite_insert
        else:
            raise RuntimeError(
                f"Atomic invitation view tracking is unsupported for {dialect_name}"
            )
        statement = insert(InvitationView).values(
            invitation_id=invitation_id,
            visitor_key_hash=visitor_key_hash,
            guest_id=guest_id,
        )
        conflict_update = {
            "set_": {
                "viewed_at": func.now(),
                "guest_id": func.coalesce(
                    statement.excluded.guest_id,
                    InvitationView.guest_id,
                ),
            }
        }
        if dialect_name == "postgresql":
            statement = statement.on_conflict_do_update(
                constraint="uq_invitation_views_invitation_visitor",
                **conflict_update,
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[
                    InvitationView.invitation_id,
                    InvitationView.visitor_key_hash,
                ],
                **conflict_update,
            )
        self.session.execute(statement)
        self.session.commit()

    def view_count(self, invitation_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count(InvitationView.id)).where(
                    InvitationView.invitation_id == invitation_id
                )
            )
            or 0
        )
