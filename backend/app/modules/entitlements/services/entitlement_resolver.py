from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.database.base import JSONValue
from app.models.entitlement_override import EntitlementOverride
from app.models.feature import Feature
from app.modules.entitlements.repositories.entitlement_repository import (
    EntitlementRepository,
)
from app.modules.templates.models.template import Template


@dataclass(frozen=True)
class EntitlementDecision:
    feature_code: str
    allowed: bool
    reason: str
    source: Literal["free", "personal_purchase", "workspace_subscription", "override"] | None = None
    plan_code: str | None = None
    expires_at: datetime | None = None
    configuration: JSONValue | None = None
    limit: int | float | None = None


class EntitlementResolver:
    """Resolve access from invitation, workspace, plan, subscription, and override state."""

    def __init__(self, session: Session) -> None:
        self.repository = EntitlementRepository(session)

    def require_template_access(
        self,
        template: Template,
        *,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        invitation_id: UUID | None = None,
    ) -> EntitlementDecision:
        if template.access_level == "free":
            return EntitlementDecision(
                feature_code="premium_templates",
                allowed=True,
                reason="free_template",
                source="free",
            )
        if template.access_level != "premium":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template access level is invalid",
            )
        return self.require_feature_access(
            "premium_templates",
            user_id=user_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )

    def require_feature_access(
        self,
        feature_code: str,
        *,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        invitation_id: UUID | None = None,
    ) -> EntitlementDecision:
        decision = self.evaluate_feature_access(
            feature_code,
            user_id=user_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        if not decision.allowed:
            detail = (
                "Entitlement data is inconsistent"
                if decision.reason == "conflicting_overrides"
                else "Required feature entitlement is unavailable"
            )
            raise HTTPException(
                status_code=(
                    status.HTTP_409_CONFLICT
                    if decision.reason == "conflicting_overrides"
                    else status.HTTP_403_FORBIDDEN
                ),
                detail=detail,
            )
        return decision

    def claim_unassigned_personal_purchase(
        self,
        *,
        user_id: UUID,
        invitation_id: UUID,
        feature_code: str,
    ) -> EntitlementDecision:
        if not self.repository.has_personal_invitation_access(user_id, invitation_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Personal purchase scope does not match invitation owner",
            )
        purchase = self.repository.lock_unassigned_personal_purchase(
            user_id,
            feature_code,
            datetime.now(timezone.utc),
        )
        if purchase is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Required feature entitlement is unavailable",
            )
        purchase.invitation_id = invitation_id
        self.repository.session.flush()
        return self.require_feature_access(
            feature_code,
            user_id=user_id,
            invitation_id=invitation_id,
        )

    def evaluate_feature_access(
        self,
        feature_code: str,
        *,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        invitation_id: UUID | None = None,
    ) -> EntitlementDecision:
        feature = self.repository.get_feature(feature_code)
        if feature is None:
            return self._denied(feature_code, "unknown_feature")
        if not feature.is_active:
            return self._denied(feature_code, "inactive_feature")

        now = datetime.now(timezone.utc)
        if workspace_id is not None:
            if invitation_id is not None or user_id is None:
                return self._denied(feature_code, "invalid_scope")
            if not self.repository.has_active_workspace_membership(workspace_id, user_id):
                return self._denied(feature_code, "no_active_workspace_membership")
            overrides = self.repository.overrides_for_workspace(
                workspace_id, feature_code, now
            )
            grants = self.repository.workspace_plan_features(workspace_id, feature_code, now)
            source: Literal["personal_purchase", "workspace_subscription"] = (
                "workspace_subscription"
            )
        elif invitation_id is not None:
            if user_id is None:
                return self._denied(feature_code, "invalid_scope")
            if not self.repository.has_personal_invitation_access(user_id, invitation_id):
                return self._denied(feature_code, "invitation_scope_mismatch")
            overrides = self.repository.overrides_for_invitation(
                invitation_id, feature_code, now
            )
            grants = self.repository.personal_plan_features(
                user_id, invitation_id, feature_code, now
            )
            source = "personal_purchase"
        else:
            return self._denied(feature_code, "invalid_scope")

        if len(overrides) > 1:
            return self._denied(feature_code, "conflicting_overrides")
        override = overrides[0] if overrides else None
        if override is not None and override.is_enabled is not None:
            if override.is_enabled:
                limit = self._override_limit(feature, override)
                if feature.feature_type == "limit" and limit is None:
                    return self._denied(feature_code, "invalid_override_limit")
                return EntitlementDecision(
                    feature_code=feature_code,
                    allowed=True,
                    reason="enabled_by_override",
                    source="override",
                    expires_at=override.expires_at,
                    limit=limit,
                )
            return EntitlementDecision(
                feature_code=feature_code,
                allowed=False,
                reason="disabled_by_override",
                source="override",
                expires_at=override.expires_at,
            )

        if not grants:
            return self._denied(feature_code, "no_active_plan_feature")

        plan, configuration, grant_term = grants[0]
        if source == "personal_purchase":
            purchase = grant_term
            if purchase.duration_type_snapshot == "fixed" and (
                purchase.duration_days_snapshot is None
                or purchase.duration_days_snapshot <= 0
                or purchase.expires_at is None
            ):
                return self._denied(feature_code, "invalid_purchase_snapshot")
            if purchase.duration_type_snapshot == "lifetime" and (
                purchase.duration_days_snapshot is not None
                or purchase.expires_at is not None
            ):
                return self._denied(feature_code, "invalid_purchase_snapshot")
            if purchase.duration_type_snapshot not in (None, "fixed", "lifetime"):
                return self._denied(feature_code, "invalid_purchase_snapshot")
            expires_at = purchase.expires_at
        else:
            expires_at = grant_term
        if not isinstance(configuration, dict):
            return self._denied(feature_code, "invalid_plan_configuration")
        limit = self._plan_limit(feature, configuration)
        if feature.feature_type == "limit" and limit is None:
            return self._denied(feature_code, "invalid_plan_configuration")
        if override is not None and override.limit_override is not None:
            limit = self._override_limit(feature, override)
            if limit is None:
                return self._denied(feature_code, "invalid_override_limit")

        return EntitlementDecision(
            feature_code=feature_code,
            allowed=True,
            reason="active_plan_feature",
            source=source,
            plan_code=plan.code,
            expires_at=expires_at,
            configuration=configuration,
            limit=limit,
        )

    @staticmethod
    def _plan_limit(
        feature: Feature,
        configuration: JSONValue,
    ) -> int | float | None:
        if feature.feature_type != "limit":
            return None
        value = configuration.get("limit")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        return value

    @classmethod
    def _override_limit(
        cls,
        feature: Feature,
        override: EntitlementOverride,
    ) -> int | float | None:
        if feature.feature_type != "limit":
            return None
        value = override.limit_override
        if isinstance(value, dict):
            value = value.get("limit")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        return value

    @staticmethod
    def _denied(feature_code: str, reason: str) -> EntitlementDecision:
        return EntitlementDecision(
            feature_code=feature_code,
            allowed=False,
            reason=reason,
        )
