"""Add versioned saved searches and durable daily scheduling."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0010"
down_revision: str | Sequence[str] | None = "20261009_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "process_watchlist_entries",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE process_watchlist_entries
        SET next_run_at = (
            CASE
                WHEN date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo')
                     + interval '6 hours' > now() AT TIME ZONE 'America/Sao_Paulo'
                THEN date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo')
                     + interval '6 hours'
                ELSE date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo')
                     + interval '1 day 6 hours'
            END
        ) AT TIME ZONE 'America/Sao_Paulo'
        WHERE active
        """
    )
    op.create_check_constraint(
        "ck_process_watchlist_schedule_matches_active",
        "process_watchlist_entries",
        "(active and next_run_at is not null) or (not active and next_run_at is null)",
    )
    op.create_index(
        "ix_process_watchlist_due",
        "process_watchlist_entries",
        ["next_run_at", "process_id"],
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "saved_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) between 1 and 160", name="ck_saved_searches_name"),
        sa.CheckConstraint("current_version >= 1", name="ck_saved_searches_version_positive"),
        sa.CheckConstraint(
            "(enabled and next_run_at is not null) or (not enabled and next_run_at is null)",
            name="ck_saved_searches_schedule_matches_enabled",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_searches"),
    )
    op.create_index(
        "ix_saved_searches_due",
        "saved_searches",
        ["next_run_at", "id"],
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "saved_search_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("saved_search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("preset_id", sa.String(length=120), nullable=False),
        sa.Column("preset_version", sa.String(length=80), nullable=False),
        sa.Column("window_mode", sa.String(length=24), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("catalog_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_saved_search_versions_positive"),
        sa.CheckConstraint(
            "length(btrim(name)) between 1 and 160", name="ck_saved_search_versions_name"
        ),
        sa.CheckConstraint(
            "window_mode in ('fixed', 'rolling_12_months')",
            name="ck_saved_search_versions_window_mode",
        ),
        sa.ForeignKeyConstraint(
            ["saved_search_id"],
            ["saved_searches.id"],
            name="fk_saved_search_versions_search",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_search_versions"),
        sa.UniqueConstraint("saved_search_id", "version", name="uq_saved_search_versions_number"),
        sa.UniqueConstraint("id", "saved_search_id", name="uq_saved_search_versions_id_search"),
    )
    op.create_index(
        "ix_saved_search_versions_search",
        "saved_search_versions",
        ["saved_search_id", "version"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_saved_search_version_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'saved_search_versions is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_saved_search_versions_append_only
        BEFORE UPDATE OR DELETE ON saved_search_versions
        FOR EACH ROW EXECUTE FUNCTION reject_saved_search_version_mutation()
        """
    )

    op.create_table(
        "schedule_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("saved_search_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_for_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("missed_from", sa.Date(), nullable=True),
        sa.Column("missed_through", sa.Date(), nullable=True),
        sa.Column("coalesced_into_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(target_kind = 'saved_search' and saved_search_id is not null "
            "and process_id is null) or "
            "(target_kind = 'watch' and process_id is not null "
            "and saved_search_id is null)",
            name="ck_schedule_dispatches_target",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'enqueued', 'blocked', 'cancelled', 'coalesced')",
            name="ck_schedule_dispatches_status",
        ),
        sa.CheckConstraint(
            "(missed_from is null and missed_through is null) or "
            "(missed_from is not null and missed_through is not null "
            "and missed_from <= missed_through)",
            name="ck_schedule_dispatches_missed_interval",
        ),
        sa.CheckConstraint(
            "(status = 'pending' and job_id is null and request_snapshot is not null) or "
            "(status = 'enqueued' and job_id is not null and request_snapshot is not null) or "
            "(status in ('blocked', 'cancelled', 'coalesced') and job_id is null)",
            name="ck_schedule_dispatches_status_fields",
        ),
        sa.ForeignKeyConstraint(
            ["saved_search_id"],
            ["saved_searches.id"],
            name="fk_schedule_dispatches_saved_search",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_schedule_dispatches_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_schedule_dispatches_job", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["coalesced_into_id"],
            ["schedule_dispatches.id"],
            name="fk_schedule_dispatches_coalesced_into",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_dispatches"),
    )
    op.create_index(
        "uq_schedule_dispatch_search_date",
        "schedule_dispatches",
        ["saved_search_id", "scheduled_for_date"],
        unique=True,
        postgresql_where=sa.text("saved_search_id is not null"),
    )
    op.create_index(
        "uq_schedule_dispatch_watch_date",
        "schedule_dispatches",
        ["process_id", "scheduled_for_date"],
        unique=True,
        postgresql_where=sa.text("process_id is not null"),
    )
    op.create_index(
        "uq_schedule_dispatch_pending_search",
        "schedule_dispatches",
        ["saved_search_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' and saved_search_id is not null"),
    )
    op.create_index(
        "uq_schedule_dispatch_pending_watch",
        "schedule_dispatches",
        ["process_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' and process_id is not null"),
    )
    op.create_index(
        "ix_schedule_dispatch_search_date",
        "schedule_dispatches",
        ["saved_search_id", "scheduled_for_date"],
    )
    op.create_index(
        "ix_schedule_dispatch_watch_date",
        "schedule_dispatches",
        ["process_id", "scheduled_for_date"],
    )
    op.create_index(
        "ix_schedule_dispatch_pending",
        "schedule_dispatches",
        ["status", "created_at", "id"],
    )

    op.create_table(
        "queue_claim_state",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("last_category", sa.String(length=16), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "last_category in ('refresh', 'non_refresh')",
            name="ck_queue_claim_state_category",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_queue_claim_state"),
    )
    op.execute("INSERT INTO queue_claim_state (key, last_category) VALUES ('main', 'non_refresh')")


def downgrade() -> None:
    op.drop_table("queue_claim_state")
    op.drop_index("ix_schedule_dispatch_pending", table_name="schedule_dispatches")
    op.drop_index("ix_schedule_dispatch_watch_date", table_name="schedule_dispatches")
    op.drop_index("ix_schedule_dispatch_search_date", table_name="schedule_dispatches")
    op.drop_index("uq_schedule_dispatch_pending_watch", table_name="schedule_dispatches")
    op.drop_index("uq_schedule_dispatch_pending_search", table_name="schedule_dispatches")
    op.drop_index("uq_schedule_dispatch_watch_date", table_name="schedule_dispatches")
    op.drop_index("uq_schedule_dispatch_search_date", table_name="schedule_dispatches")
    op.drop_table("schedule_dispatches")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_saved_search_versions_append_only ON saved_search_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_saved_search_version_mutation()")
    op.drop_index("ix_saved_search_versions_search", table_name="saved_search_versions")
    op.drop_table("saved_search_versions")
    op.drop_index("ix_saved_searches_due", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_index("ix_process_watchlist_due", table_name="process_watchlist_entries")
    op.drop_constraint(
        "ck_process_watchlist_schedule_matches_active",
        "process_watchlist_entries",
        type_="check",
    )
    op.drop_column("process_watchlist_entries", "next_run_at")
