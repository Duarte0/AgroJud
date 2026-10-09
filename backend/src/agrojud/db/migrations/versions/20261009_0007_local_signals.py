"""Persist versioned local signal runs and their process-level publications."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0007"
down_revision: str | Sequence[str] | None = "20261009_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type in ('discovery', 'refresh_number', 'reprocess_rules')",
    )
    op.alter_column("jobs", "collection_id", nullable=True)

    op.create_table(
        "signal_run_processes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("processed_input_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'completed', 'stale', 'not_evaluated')",
            name="ck_signal_run_processes_status",
        ),
        sa.CheckConstraint("input_count >= 0", name="ck_signal_run_processes_input_count"),
        sa.CheckConstraint(
            "processed_input_count between 0 and input_count",
            name="ck_signal_run_processes_processed_count",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_run_processes_job", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_run_processes_process",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_run_processes"),
        sa.UniqueConstraint("job_id", "process_id", name="uq_signal_run_processes_pair"),
    )
    op.create_index(
        "ix_signal_run_processes_process", "signal_run_processes", ["process_id", "job_id"]
    )

    op.create_table(
        "signal_run_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("latest_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("movement_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_complete", sa.Boolean(), nullable=False),
        sa.Column("evidence_stale", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'completed', 'not_evaluated')",
            name="ck_signal_run_inputs_status",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_run_inputs_job", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_run_inputs_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_signal_run_inputs_representation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "latest_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_signal_run_inputs_latest_version",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "input_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_signal_run_inputs_input_version",
        ),
        sa.ForeignKeyConstraint(
            ["movement_snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_signal_run_inputs_snapshot_representation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_run_inputs"),
        sa.UniqueConstraint(
            "job_id", "representation_id", name="uq_signal_run_inputs_representation"
        ),
    )
    op.create_index("ix_signal_run_inputs_job_cursor", "signal_run_inputs", ["job_id", "id"])
    op.create_index("ix_signal_run_inputs_process", "signal_run_inputs", ["job_id", "process_id"])

    op.create_table(
        "process_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("movement_occurrence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("rule_version", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=8), nullable=False),
        sa.Column("enablement_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("result_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("evidence_stale", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evidence_kind in ('movement_occurrence', 'process_attribute', "
            "'representation_attribute')",
            name="ck_process_signals_evidence_kind",
        ),
        sa.CheckConstraint(
            "evidence_kind <> 'movement_occurrence' or movement_occurrence_id = evidence_id",
            name="ck_process_signals_movement_evidence_id",
        ),
        sa.CheckConstraint(
            "environment in ('demo', 'real')", name="ck_process_signals_environment"
        ),
        sa.CheckConstraint(
            "result_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_process_signals_fingerprint_hex"
        ),
        sa.CheckConstraint("length(btrim(rule_id)) > 0", name="ck_process_signals_rule_nonempty"),
        sa.CheckConstraint(
            "length(btrim(rule_version)) > 0", name="ck_process_signals_rule_version_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["process_id"], ["processes.id"], name="fk_process_signals_process", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_process_signals_representation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["movement_occurrence_id"],
            ["movement_occurrences.id"],
            name="fk_process_signals_occurrence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_run_id"],
            ["jobs.id"],
            name="fk_process_signals_created_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_run_id"],
            ["jobs.id"],
            name="fk_process_signals_published_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_version_id"],
            ["representation_versions.id"],
            name="fk_process_signals_evidence_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_snapshot_id"],
            ["movement_snapshots.id"],
            name="fk_process_signals_evidence_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_process_signals"),
        sa.UniqueConstraint(
            "rule_id",
            "rule_version",
            "evidence_id",
            "result_fingerprint",
            name="uq_process_signals_rule_evidence_result",
        ),
    )
    op.create_index(
        "ix_process_signals_process_current",
        "process_signals",
        ["process_id", "is_current", "category"],
    )
    op.create_index(
        "ix_process_signals_rule_version", "process_signals", ["rule_id", "rule_version"]
    )

    op.create_table(
        "signal_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("rule_version", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("matched_signal_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("movement_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome in ('matched', 'no_match', 'not_evaluated')",
            name="ck_signal_evaluations_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_evaluations_job", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["input_id"],
            ["signal_run_inputs.id"],
            name="fk_signal_evaluations_input",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_evaluations_process",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_evaluations"),
        sa.UniqueConstraint(
            "job_id",
            "input_id",
            "rule_id",
            "rule_version",
            name="uq_signal_evaluations_run_input_rule",
        ),
    )
    op.create_index(
        "ix_signal_evaluations_process_rule",
        "signal_evaluations",
        ["process_id", "rule_id", "evaluated_at"],
    )

    op.execute(
        """
        CREATE FUNCTION agrojud_guard_signal_run_input_snapshot() RETURNS trigger AS $$
        BEGIN
            IF ROW(NEW.job_id, NEW.process_id, NEW.representation_id, NEW.latest_version_id,
                   NEW.input_version_id, NEW.movement_snapshot_id, NEW.input_complete,
                   NEW.evidence_stale)
               IS DISTINCT FROM
               ROW(OLD.job_id, OLD.process_id, OLD.representation_id, OLD.latest_version_id,
                   OLD.input_version_id, OLD.movement_snapshot_id, OLD.input_complete,
                   OLD.evidence_stale) THEN
                RAISE EXCEPTION 'signal run input snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_signal_run_inputs_snapshot_immutable
        BEFORE UPDATE ON signal_run_inputs
        FOR EACH ROW EXECUTE FUNCTION agrojud_guard_signal_run_input_snapshot()
        """
    )
    op.execute(
        """
        CREATE FUNCTION agrojud_prevent_signal_evaluation_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'signal evaluations are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_signal_evaluations_append_only
        BEFORE UPDATE OR DELETE ON signal_evaluations
        FOR EACH ROW EXECUTE FUNCTION agrojud_prevent_signal_evaluation_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_signal_evaluations_append_only ON signal_evaluations")
    op.execute("DROP FUNCTION IF EXISTS agrojud_prevent_signal_evaluation_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_signal_run_inputs_snapshot_immutable ON signal_run_inputs"
    )
    op.execute("DROP FUNCTION IF EXISTS agrojud_guard_signal_run_input_snapshot()")
    op.drop_index("ix_signal_evaluations_process_rule", table_name="signal_evaluations")
    op.drop_table("signal_evaluations")
    op.drop_index("ix_process_signals_rule_version", table_name="process_signals")
    op.drop_index("ix_process_signals_process_current", table_name="process_signals")
    op.drop_table("process_signals")
    op.drop_index("ix_signal_run_inputs_process", table_name="signal_run_inputs")
    op.drop_index("ix_signal_run_inputs_job_cursor", table_name="signal_run_inputs")
    op.drop_table("signal_run_inputs")
    op.drop_index("ix_signal_run_processes_process", table_name="signal_run_processes")
    op.drop_table("signal_run_processes")
    op.alter_column("jobs", "collection_id", nullable=False)
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type", "jobs", "job_type in ('discovery', 'refresh_number')"
    )
