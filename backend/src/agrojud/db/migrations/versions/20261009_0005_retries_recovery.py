"""Persist HTTP retry policy, source cooldown, and recovery commands."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0005"
down_revision: str | Sequence[str] | None = "20261009_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_jobs_available_claim", table_name="jobs")
    op.alter_column("jobs", "available_at", new_column_name="next_attempt_at")
    op.add_column(
        "jobs",
        sa.Column("retry_cycle", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("page_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("persistence_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("recovery_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("cursor_invalid", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("predecessor_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint("ck_jobs_retry_cycle_positive", "jobs", "retry_cycle >= 1")
    op.create_check_constraint(
        "ck_jobs_page_attempt_count", "jobs", "page_attempt_count between 0 and 5"
    )
    op.create_check_constraint(
        "ck_jobs_persistence_attempt_count",
        "jobs",
        "persistence_attempt_count between 0 and 5",
    )
    op.create_check_constraint("ck_jobs_recovery_count_nonnegative", "jobs", "recovery_count >= 0")
    op.create_check_constraint(
        "ck_jobs_predecessor_not_self",
        "jobs",
        "predecessor_job_id is null or predecessor_job_id <> id",
    )
    op.create_foreign_key(
        "fk_jobs_predecessor_job_id_jobs",
        "jobs",
        "jobs",
        ["predecessor_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_jobs_next_attempt_claim",
        "jobs",
        ["status", "next_attempt_at", "created_at"],
        postgresql_where=sa.text("status in ('queued', 'retry_wait')"),
    )

    op.create_table(
        "source_rate_limits",
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(source)) > 0", name="ck_source_rate_limits_source_nonempty"
        ),
        sa.PrimaryKeyConstraint("source", name="pk_source_rate_limits"),
    )


def downgrade() -> None:
    op.drop_table("source_rate_limits")
    op.drop_index("ix_jobs_next_attempt_claim", table_name="jobs")
    op.drop_constraint("fk_jobs_predecessor_job_id_jobs", "jobs", type_="foreignkey")
    op.drop_constraint("ck_jobs_predecessor_not_self", "jobs", type_="check")
    op.drop_constraint("ck_jobs_recovery_count_nonnegative", "jobs", type_="check")
    op.drop_constraint("ck_jobs_persistence_attempt_count", "jobs", type_="check")
    op.drop_constraint("ck_jobs_page_attempt_count", "jobs", type_="check")
    op.drop_constraint("ck_jobs_retry_cycle_positive", "jobs", type_="check")
    op.drop_column("jobs", "predecessor_job_id")
    op.drop_column("jobs", "cursor_invalid")
    op.drop_column("jobs", "recovery_count")
    op.drop_column("jobs", "page_attempt_count")
    op.drop_column("jobs", "persistence_attempt_count")
    op.drop_column("jobs", "retry_cycle")
    op.alter_column("jobs", "next_attempt_at", new_column_name="available_at")
    op.create_index(
        "ix_jobs_available_claim",
        "jobs",
        ["status", "available_at", "created_at"],
        postgresql_where=sa.text("status in ('queued', 'retry_wait')"),
    )
