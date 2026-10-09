"""Add persistent jobs, attempts, lifecycle events, and initial checkpoints."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0004"
down_revision: str | Sequence[str] | None = "20261009_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=24), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("tribunal", sa.String(length=40), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("parameters_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="queued", nullable=False),
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("job_type in ('discovery', 'refresh_number')", name="ck_jobs_type"),
        sa.CheckConstraint("mode in ('demo', 'real')", name="ck_jobs_mode"),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'retry_wait', 'completed', 'partial', "
            "'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("length(btrim(source)) > 0", name="ck_jobs_source_nonempty"),
        sa.CheckConstraint("length(btrim(tribunal)) > 0", name="ck_jobs_tribunal_nonempty"),
        sa.CheckConstraint("operation_key ~ '^[0-9a-f]{64}$'", name="ck_jobs_operation_key_hex"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_nonnegative"),
        sa.CheckConstraint("event_count >= 0", name="ck_jobs_event_count_nonnegative"),
        sa.CheckConstraint(
            "(status = 'running' and lease_token is not null and lease_owner is not null "
            "and lease_expires_at is not null and heartbeat_at is not null) or "
            "(status <> 'running' and lease_token is null and lease_owner is null "
            "and lease_expires_at is null and heartbeat_at is null)",
            name="ck_jobs_lease_matches_status",
        ),
        sa.CheckConstraint(
            "status = 'running' or not cancel_requested", name="ck_jobs_cancel_only_running"
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_jobs_collection_id_collections",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint("collection_id", name="uq_jobs_collection_id"),
    )
    op.create_index(
        "uq_jobs_active_operation_key",
        "jobs",
        ["operation_key"],
        unique=True,
        postgresql_where=sa.text("status in ('queued', 'running', 'retry_wait')"),
    )
    op.create_index(
        "ix_jobs_available_claim",
        "jobs",
        ["status", "available_at", "created_at"],
        postgresql_where=sa.text("status in ('queued', 'retry_wait')"),
    )
    op.create_index(
        "ix_jobs_expired_claim",
        "jobs",
        ["lease_expires_at", "created_at"],
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.String(length=240), nullable=True),
        sa.CheckConstraint("attempt_number >= 1", name="ck_job_attempts_number_positive"),
        sa.CheckConstraint(
            "(finished_at is null and outcome is null) or "
            "(finished_at is not null and outcome in "
            "('completed', 'partial', 'failed', 'cancelled', 'retry_wait', 'lease_expired'))",
            name="ck_job_attempts_outcome_consistent",
        ),
        sa.CheckConstraint(
            "error_code is null or length(btrim(error_code)) > 0",
            name="ck_job_attempts_error_code_nonempty",
        ),
        sa.CheckConstraint(
            "error_summary is null or length(btrim(error_summary)) > 0",
            name="ck_job_attempts_error_summary_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_attempts_job_id_jobs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_attempts"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
    )
    op.create_index(
        "uq_job_attempts_active_job",
        "job_attempts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("finished_at is null"),
    )
    op.create_index("ix_job_attempts_job_started", "job_attempts", ["job_id", "started_at"])

    op.create_table(
        "job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("event_number >= 1", name="ck_job_events_number_positive"),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_job_events_type_nonempty"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_events_job_id_jobs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("job_id", "event_number", name="uq_job_events_number"),
    )
    op.create_index("ix_job_events_job_created", "job_events", ["job_id", "created_at"])

    op.create_table(
        "job_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_page", sa.Integer(), server_default="1", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("next_page >= 1", name="ck_job_checkpoints_next_page_positive"),
        sa.CheckConstraint("revision >= 0", name="ck_job_checkpoints_revision_nonnegative"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_checkpoints_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_checkpoints"),
        sa.UniqueConstraint("job_id", name="uq_job_checkpoints_job_id"),
    )

    op.execute(
        """
        CREATE FUNCTION agrojud_prevent_job_identity_update() RETURNS trigger AS $$
        BEGIN
            IF ROW(NEW.collection_id, NEW.job_type, NEW.mode, NEW.source, NEW.tribunal,
                   NEW.operation_key, NEW.parameters_snapshot)
               IS DISTINCT FROM
               ROW(OLD.collection_id, OLD.job_type, OLD.mode, OLD.source, OLD.tribunal,
                   OLD.operation_key, OLD.parameters_snapshot) THEN
                RAISE EXCEPTION 'job identity and parameter snapshot are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_jobs_identity_immutable
        BEFORE UPDATE ON jobs
        FOR EACH ROW EXECUTE FUNCTION agrojud_prevent_job_identity_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION agrojud_prevent_job_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'job events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_job_events_append_only
        BEFORE UPDATE OR DELETE ON job_events
        FOR EACH ROW EXECUTE FUNCTION agrojud_prevent_job_event_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION agrojud_guard_job_attempt_update() RETURNS trigger AS $$
        BEGIN
            IF OLD.finished_at IS NOT NULL
               OR ROW(NEW.job_id, NEW.attempt_number, NEW.worker_id, NEW.started_at)
                  IS DISTINCT FROM
                  ROW(OLD.job_id, OLD.attempt_number, OLD.worker_id, OLD.started_at)
               OR NEW.finished_at IS NULL THEN
                RAISE EXCEPTION 'job attempt history is immutable after it is closed';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_job_attempts_close_once
        BEFORE UPDATE OR DELETE ON job_attempts
        FOR EACH ROW EXECUTE FUNCTION agrojud_guard_job_attempt_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_job_attempts_close_once ON job_attempts")
    op.execute("DROP FUNCTION IF EXISTS agrojud_guard_job_attempt_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_job_events_append_only ON job_events")
    op.execute("DROP FUNCTION IF EXISTS agrojud_prevent_job_event_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_identity_immutable ON jobs")
    op.execute("DROP FUNCTION IF EXISTS agrojud_prevent_job_identity_update()")
    op.drop_table("job_checkpoints")
    op.drop_index("ix_job_events_job_created", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_job_attempts_job_started", table_name="job_attempts")
    op.drop_index("uq_job_attempts_active_job", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_expired_claim", table_name="jobs")
    op.drop_index("ix_jobs_available_claim", table_name="jobs")
    op.drop_index("uq_jobs_active_operation_key", table_name="jobs")
    op.drop_table("jobs")
