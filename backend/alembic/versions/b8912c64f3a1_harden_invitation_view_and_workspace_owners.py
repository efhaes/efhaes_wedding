"""harden invitation view and workspace owner integrity

Revision ID: b8912c64f3a1
Revises: a34c9d12ef76
Create Date: 2026-10-31 21:35:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8912c64f3a1"
down_revision: Union[str, Sequence[str], None] = "a34c9d12ef76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid_view = connection.execute(
        sa.text(
            """
            SELECT iv.id
            FROM invitation_views AS iv
            LEFT JOIN guests AS g ON g.id = iv.guest_id
            WHERE iv.guest_id IS NOT NULL
              AND (g.id IS NULL OR g.invitation_id <> iv.invitation_id)
            LIMIT 1
            """
        )
    ).first()
    if invalid_view is not None:
        raise RuntimeError(
            "Cannot add invitation/guest integrity constraint: "
            "invitation_views contains a guest from another invitation. "
            "Run the documented integrity query and resolve rows before retrying."
        )

    invalid_workspace = connection.execute(
        sa.text(
            """
            SELECT w.id
            FROM workspaces AS w
            LEFT JOIN workspace_members AS wm
              ON wm.workspace_id = w.id
            GROUP BY w.id, w.owner_id
            HAVING count(*) FILTER (
                WHERE wm.user_id = w.owner_id
                  AND wm.role = 'owner'
                  AND wm.status = 'active'
            ) <> 1
               OR count(*) FILTER (
                WHERE wm.role = 'owner'
                  AND wm.status = 'active'
            ) <> 1
            LIMIT 1
            """
        )
    ).first()
    if invalid_workspace is not None:
        raise RuntimeError(
            "Cannot add workspace owner integrity triggers: "
            "a workspace does not have exactly one active owner membership "
            "matching owner_id. Resolve existing rows before retrying."
        )

    op.create_foreign_key(
        "fk_workspaces_owner_membership",
        "workspaces",
        "workspace_members",
        ["id", "owner_id"],
        ["workspace_id", "user_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_invitation_views_invitation_guest",
        "invitation_views",
        "guests",
        ["invitation_id", "guest_id"],
        ["invitation_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.execute(
        """
        CREATE FUNCTION assert_workspace_owner_membership(target_workspace_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_owner_id uuid;
            matching_owner_count integer;
            active_owner_count integer;
        BEGIN
            SELECT owner_id
              INTO expected_owner_id
              FROM workspaces
             WHERE id = target_workspace_id;

            IF NOT FOUND THEN
                RETURN;
            END IF;

            SELECT
                count(*) FILTER (
                    WHERE user_id = expected_owner_id
                      AND role = 'owner'
                      AND status = 'active'
                ),
                count(*) FILTER (
                    WHERE role = 'owner'
                      AND status = 'active'
                )
              INTO matching_owner_count, active_owner_count
              FROM workspace_members
             WHERE workspace_id = target_workspace_id;

            IF matching_owner_count <> 1 OR active_owner_count <> 1 THEN
                RAISE EXCEPTION
                    'workspace % must have exactly one active owner membership matching owner_id',
                    target_workspace_id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_workspace_owner_membership_consistent';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION check_workspace_owner_membership_constraint()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'workspaces' THEN
                IF TG_OP <> 'DELETE' THEN
                    PERFORM assert_workspace_owner_membership(NEW.id);
                END IF;
                RETURN NULL;
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                PERFORM assert_workspace_owner_membership(OLD.workspace_id);
            END IF;
            IF TG_OP = 'INSERT' THEN
                PERFORM assert_workspace_owner_membership(NEW.workspace_id);
            ELSIF TG_OP = 'UPDATE'
               AND OLD.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
                PERFORM assert_workspace_owner_membership(NEW.workspace_id);
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ck_workspace_owner_from_workspace
        AFTER INSERT OR UPDATE OR DELETE ON workspaces
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION check_workspace_owner_membership_constraint();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ck_workspace_owner_from_membership
        AFTER INSERT OR UPDATE OR DELETE ON workspace_members
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION check_workspace_owner_membership_constraint();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS ck_workspace_owner_from_membership ON workspace_members"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_workspace_owner_from_workspace ON workspaces"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS check_workspace_owner_membership_constraint()"
    )
    op.execute("DROP FUNCTION IF EXISTS assert_workspace_owner_membership(uuid)")
    op.drop_constraint(
        "fk_invitation_views_invitation_guest",
        "invitation_views",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_workspaces_owner_membership",
        "workspaces",
        type_="foreignkey",
    )
