"""Add independent multi-tribunal execution groups and dispatch references."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0011"
down_revision: str | Sequence[str] | None = "20261009_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=12), server_default="queued", nullable=False),
        sa.Column("request_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("matrix_sha256", sa.String(length=64), nullable=True),
        sa.Column("allowlist_version", sa.String(length=40), nullable=True),
        sa.Column("catalog_version", sa.String(length=40), nullable=True),
        sa.Column("tpu_version", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind in ('discovery', 'refresh_number')", name="ck_execution_groups_kind"
        ),
        sa.CheckConstraint("mode in ('demo', 'real')", name="ck_execution_groups_mode"),
        sa.CheckConstraint(
            "state in ('queued', 'running', 'completed', 'partial', 'failed', "
            "'blocked', 'cancelled')",
            name="ck_execution_groups_state",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_groups"),
    )
    op.create_index("ix_execution_groups_created", "execution_groups", ["created_at", "id"])
    op.create_index(
        "uq_execution_groups_active_idempotency",
        "execution_groups",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key is not null and state in ('queued', 'running')"),
    )

    op.create_table(
        "execution_group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tribunal", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("availability_state", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state in ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'COMPLETED', 'PARTIAL', 'FAILED', "
            "'CANCELLED', 'BLOCKED_NOT_VALIDATED', 'NOT_RUN')",
            name="ck_execution_group_members_state",
        ),
        sa.CheckConstraint(
            "length(btrim(tribunal)) > 0", name="ck_execution_group_members_tribunal"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["execution_groups.id"],
            name="fk_execution_group_members_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_execution_group_members_job", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_group_members"),
        sa.UniqueConstraint(
            "group_id", "tribunal", name="uq_execution_group_members_group_tribunal"
        ),
    )
    op.create_index("ix_execution_group_members_job", "execution_group_members", ["job_id"])
    op.create_index(
        "ix_execution_group_members_group_state", "execution_group_members", ["group_id", "state"]
    )

    op.add_column(
        "schedule_dispatches",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_schedule_dispatches_group",
        "schedule_dispatches",
        "execution_groups",
        ["group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_schedule_dispatches_status_fields", "schedule_dispatches", type_="check")
    op.create_check_constraint(
        "ck_schedule_dispatches_status_fields",
        "schedule_dispatches",
        "(status = 'pending' and job_id is null and group_id is null "
        "and request_snapshot is not null) or "
        "(status = 'enqueued' and ((job_id is not null and group_id is null) or "
        "(job_id is null and group_id is not null)) and request_snapshot is not null) or "
        "(status in ('blocked', 'cancelled', 'coalesced') and job_id is null and group_id is null)",
    )

    op.execute(
        """
        CREATE FUNCTION refresh_execution_group_state(target_group_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            member_states text[];
            next_state text;
            has_success boolean;
            has_issue boolean;
        BEGIN
            SELECT array_agg(
                CASE
                    WHEN jobs.status = 'queued' THEN 'QUEUED'
                    WHEN jobs.status = 'running' THEN 'RUNNING'
                    WHEN jobs.status = 'retry_wait' THEN 'RETRY_WAIT'
                    WHEN jobs.status = 'completed' THEN 'COMPLETED'
                    WHEN jobs.status = 'partial' THEN 'PARTIAL'
                    WHEN jobs.status = 'failed' THEN 'FAILED'
                    WHEN jobs.status = 'cancelled' THEN 'CANCELLED'
                    ELSE members.state
                END ORDER BY members.tribunal
            )
            INTO member_states
            FROM execution_group_members AS members
            LEFT JOIN jobs ON jobs.id = members.job_id
            WHERE members.group_id = target_group_id;

            IF member_states IS NULL OR cardinality(member_states) = 0 THEN
                next_state := 'blocked';
            ELSE
                has_success := member_states && ARRAY['COMPLETED', 'PARTIAL']::text[];
                has_issue := member_states && ARRAY[
                    'PARTIAL', 'FAILED', 'BLOCKED_NOT_VALIDATED', 'NOT_RUN', 'CANCELLED'
                ]::text[];
                IF member_states && ARRAY['RUNNING', 'RETRY_WAIT']::text[] THEN
                    next_state := 'running';
                ELSIF member_states && ARRAY['QUEUED']::text[] THEN
                    next_state := 'queued';
                ELSIF has_success AND has_issue THEN
                    next_state := 'partial';
                ELSIF member_states && ARRAY['PARTIAL']::text[] THEN
                    next_state := 'partial';
                ELSIF member_states <@ ARRAY['BLOCKED_NOT_VALIDATED', 'NOT_RUN']::text[] THEN
                    next_state := 'blocked';
                ELSIF member_states && ARRAY['FAILED']::text[] AND NOT has_success THEN
                    next_state := 'failed';
                ELSIF member_states <@ ARRAY[
                    'CANCELLED', 'BLOCKED_NOT_VALIDATED', 'NOT_RUN'
                ]::text[] THEN
                    next_state := CASE
                        WHEN member_states && ARRAY['CANCELLED']::text[] THEN 'cancelled'
                        ELSE 'blocked'
                    END;
                ELSIF member_states <@ ARRAY['COMPLETED']::text[] THEN
                    next_state := 'completed';
                ELSE
                    next_state := 'running';
                END IF;
            END IF;

            UPDATE execution_groups
            SET state = next_state,
                finished_at = CASE
                    WHEN next_state IN ('completed', 'partial', 'failed', 'blocked', 'cancelled')
                    THEN COALESCE(finished_at, clock_timestamp())
                    ELSE NULL
                END,
                updated_at = clock_timestamp()
            WHERE id = target_group_id AND state IS DISTINCT FROM next_state;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION refresh_execution_group_state_from_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM refresh_execution_group_state(OLD.group_id);
                RETURN OLD;
            END IF;
            PERFORM refresh_execution_group_state(NEW.group_id);
            IF TG_OP = 'UPDATE' AND OLD.group_id IS DISTINCT FROM NEW.group_id THEN
                PERFORM refresh_execution_group_state(OLD.group_id);
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_group_state_from_member
        AFTER INSERT OR DELETE OR UPDATE OF group_id, job_id, state ON execution_group_members
        FOR EACH ROW EXECUTE FUNCTION refresh_execution_group_state_from_member()
        """
    )
    op.execute(
        """
        CREATE FUNCTION refresh_execution_group_state_from_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            affected_group_id uuid;
        BEGIN
            FOR affected_group_id IN
                SELECT DISTINCT group_id
                FROM execution_group_members
                WHERE job_id = NEW.id
            LOOP
                PERFORM refresh_execution_group_state(affected_group_id);
            END LOOP;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_group_state_from_job
        AFTER UPDATE OF status ON jobs
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION refresh_execution_group_state_from_job()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_execution_group_state_from_job ON jobs")
    op.execute("DROP FUNCTION IF EXISTS refresh_execution_group_state_from_job()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_execution_group_state_from_member ON execution_group_members"
    )
    op.execute("DROP FUNCTION IF EXISTS refresh_execution_group_state_from_member()")
    op.execute("DROP FUNCTION IF EXISTS refresh_execution_group_state(uuid)")
    op.drop_constraint("ck_schedule_dispatches_status_fields", "schedule_dispatches", type_="check")
    op.create_check_constraint(
        "ck_schedule_dispatches_status_fields",
        "schedule_dispatches",
        "(status = 'pending' and job_id is null and request_snapshot is not null) or "
        "(status = 'enqueued' and job_id is not null and request_snapshot is not null) or "
        "(status in ('blocked', 'cancelled', 'coalesced') and job_id is null)",
    )
    op.drop_constraint("fk_schedule_dispatches_group", "schedule_dispatches", type_="foreignkey")
    op.drop_column("schedule_dispatches", "group_id")
    op.drop_index("ix_execution_group_members_group_state", table_name="execution_group_members")
    op.drop_index("ix_execution_group_members_job", table_name="execution_group_members")
    op.drop_table("execution_group_members")
    op.drop_index("ix_execution_groups_created", table_name="execution_groups")
    op.drop_index("uq_execution_groups_active_idempotency", table_name="execution_groups")
    op.drop_table("execution_groups")
