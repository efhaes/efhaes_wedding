from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.feature import Feature
from app.models.plan import Plan
from app.modules.entitlements.schemas.catalog import PlanConfiguration


def _insert_do_nothing(session: Session, model: Any, values: dict[str, Any]) -> int:
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(model).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(model).values(**values)
    else:
        raise RuntimeError(f"Catalog seed does not support dialect {dialect!r}")
    result = session.execute(
        statement.on_conflict_do_nothing(index_elements=["code"]).returning(model.id)
    )
    return int(result.scalar_one_or_none() is not None)


def seed_catalog(session: Session) -> dict[str, int]:
    plans = (
        PlanConfiguration(
            code="free",
            name="Free",
            description="Pending price, duration, and feature policy configuration.",
            billing_type="one_time",
            duration_type="pending",
        ),
        PlanConfiguration(
            code="premium_personal",
            name="Premium Personal",
            description="Pending price, duration, and feature policy configuration.",
            billing_type="one_time",
            duration_type="pending",
        ),
        PlanConfiguration(
            code="workspace",
            name="Workspace Plan",
            description="Pending price and feature policy configuration.",
            billing_type="subscription",
            duration_type="recurring",
        ),
    )
    plans_added = sum(
        _insert_do_nothing(
            session,
            Plan,
            plan.model_dump(),
        )
        for plan in plans
    )

    premium_templates_added = _insert_do_nothing(
        session,
        Feature,
        {
            "code": "premium_templates",
            "name": "Premium templates",
            "description": "Access to templates marked premium.",
            "feature_type": "boolean",
            "is_active": True,
        },
    )
    return {
        "plans_added": plans_added,
        "features_added": premium_templates_added,
    }


def main() -> None:
    from app.database.connection import SessionLocal

    with SessionLocal() as session:
        result = seed_catalog(session)
        session.commit()
        print(
            "Catalog seed complete: "
            f"{result['plans_added']} plans, {result['features_added']} features inserted."
        )


if __name__ == "__main__":
    main()
