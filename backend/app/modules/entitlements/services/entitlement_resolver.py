from datetime import datetime, timezone
from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.entitlement_override import EntitlementOverride
from app.modules.entitlements.repositories.entitlement_repository import (
    EntitlementRepository,
)
from app.modules.templates.models.template import Template


class EntitlementResolver:
    """Resolve premium access by invitation-scoped purchase or workspace scope.

    Personal purchases are tied to an existing invitation, so they cannot grant
    access while creating a new personal invitation.
    """

    def __init__(self, session: Session) -> None:
        self.repository = EntitlementRepository(session)

    def require_template_access(
        self,
        template: Template,
        *,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        invitation_id: UUID | None = None,
    ) -> None:
        if template.access_level == "free":
            return
        if template.access_level != "premium":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template access level is invalid",
            )

        if workspace_id is not None and user_id is not None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Entitlement scope is ambiguous",
            )

        if workspace_id is not None:
            allowed = self._workspace_access(workspace_id)
        elif user_id is not None and invitation_id is not None:
            allowed = self._personal_access(user_id, invitation_id)
        else:
            allowed = False

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Premium template entitlement is required",
            )

    def _personal_access(
        self,
        user_id: UUID,
        invitation_id: UUID,
    ) -> bool:
        override = self._override_decision(
            self.repository.overrides_for_invitation(invitation_id)
        )
        if override is not None:
            return override
        return self.repository.has_active_personal_purchase(
            user_id,
            invitation_id,
            datetime.now(timezone.utc),
        )

    def _workspace_access(self, workspace_id: UUID) -> bool:
        override = self._override_decision(
            self.repository.overrides_for_workspace(workspace_id)
        )
        if override is not None:
            return override
        return self.repository.has_active_workspace_subscription(
            workspace_id,
            datetime.now(timezone.utc),
        )

    @staticmethod
    def _override_decision(
        overrides: Sequence[EntitlementOverride],
    ) -> bool | None:
        decisions = {
            override.is_enabled
            for override in overrides
            if override.is_enabled is not None
        }
        if not decisions:
            return None
        if len(decisions) != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Premium template entitlement overrides conflict",
            )
        return decisions.pop()
