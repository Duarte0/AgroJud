"""Add per-cycle movement baselines and the reviewable process news feed."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0009"
down_revision: str | Sequence[str] | None = "20261009_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_representations_id_process", "representations", ["id", "process_id"]
    )
    op.create_table(
        "process_watch_cycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("cycle_number >= 1", name="ck_process_watch_cycles_number_positive"),
        sa.CheckConstraint(
            "ended_at is null or ended_at >= started_at",
            name="ck_process_watch_cycles_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process_watchlist_entries.process_id"],
            name="fk_process_watch_cycles_watch_entry",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_process_watch_cycles"),
        sa.UniqueConstraint("id", "process_id", name="uq_process_watch_cycles_id_process"),
        sa.UniqueConstraint("process_id", "cycle_number", name="uq_process_watch_cycles_number"),
    )
    op.create_index(
        "uq_process_watch_cycles_active_process",
        "process_watch_cycles",
        ["process_id"],
        unique=True,
        postgresql_where=sa.text("ended_at is null"),
    )
    op.create_index(
        "ix_process_watch_cycles_process_number",
        "process_watch_cycles",
        ["process_id", "cycle_number"],
    )

    op.create_table(
        "representation_watch_baselines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("watch_cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("normalizer_version", sa.String(length=80), nullable=True),
        sa.Column("established_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state in ('pending', 'established')", name="ck_representation_watch_baselines_state"
        ),
        sa.CheckConstraint(
            "(state = 'pending' and version_id is null and snapshot_id is null "
            "and normalizer_version is null and established_at is null) or "
            "(state = 'established' and version_id is not null and snapshot_id is not null "
            "and normalizer_version is not null and established_at is not null)",
            name="ck_representation_watch_baselines_state_fields",
        ),
        sa.ForeignKeyConstraint(
            ["watch_cycle_id", "process_id"],
            ["process_watch_cycles.id", "process_watch_cycles.process_id"],
            name="fk_representation_watch_baselines_cycle_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "process_id"],
            ["representations.id", "representations.process_id"],
            name="fk_representation_watch_baselines_representation_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_representation_watch_baselines_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_representation_watch_baselines_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_representation_watch_baselines"),
        sa.UniqueConstraint(
            "watch_cycle_id",
            "representation_id",
            name="uq_representation_watch_baselines_cycle_representation",
        ),
    )
    op.create_index(
        "ix_representation_watch_baselines_cycle_state",
        "representation_watch_baselines",
        ["watch_cycle_id", "state"],
    )

    op.create_table(
        "process_news",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("watch_cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("occurrence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_date_original", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_date_status", sa.String(length=24), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category in ('NEW_OBSERVATION', 'ALTERATION_OBSERVED', 'NEW_REPRESENTATION')",
            name="ck_process_news_category",
        ),
        sa.CheckConstraint("status in ('pending', 'reviewed')", name="ck_process_news_status"),
        sa.CheckConstraint(
            "source_date_status in "
            "('unknown', 'missing', 'null', 'timezone_aware', 'timezone_ambiguous', 'unparseable')",
            name="ck_process_news_date_status",
        ),
        sa.CheckConstraint(
            "provenance in ('ingestion', 'quarantine_reprocess')",
            name="ck_process_news_provenance",
        ),
        sa.CheckConstraint(
            "length(btrim(identity_key)) > 0", name="ck_process_news_identity_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "process_id"],
            ["representations.id", "representations.process_id"],
            name="fk_process_news_representation_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["watch_cycle_id", "process_id"],
            ["process_watch_cycles.id", "process_watch_cycles.process_id"],
            name="fk_process_news_cycle_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "representation_id"],
            ["movement_occurrences.id", "movement_occurrences.representation_id"],
            name="fk_process_news_occurrence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "origin_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_process_news_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["origin_snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_process_news_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_process_news"),
        sa.UniqueConstraint(
            "process_id",
            "representation_id",
            "identity_key",
            "category",
            name="uq_process_news_identity_category",
        ),
    )
    op.create_index("ix_process_news_feed", "process_news", ["first_observed_at", "id"])
    op.create_index(
        "ix_process_news_status_feed", "process_news", ["status", "first_observed_at", "id"]
    )
    op.create_index(
        "ix_process_news_category_feed", "process_news", ["category", "first_observed_at", "id"]
    )
    op.create_index(
        "ix_process_news_process_feed", "process_news", ["process_id", "first_observed_at", "id"]
    )

    op.execute(
        """
        INSERT INTO process_watch_cycles
            (id, process_id, cycle_number, started_at, ended_at)
        SELECT gen_random_uuid(), entry.process_id,
               GREATEST((
                   SELECT count(*)::integer
                   FROM process_watchlist_history AS history
                   WHERE history.process_id = entry.process_id
                     AND history.action = 'included'
               ), 1),
               entry.included_at, NULL
        FROM process_watchlist_entries AS entry
        WHERE entry.active
        """
    )
    op.execute(
        """
        INSERT INTO representation_watch_baselines
            (id, watch_cycle_id, process_id, representation_id, state, version_id,
             snapshot_id, normalizer_version, established_at)
        SELECT gen_random_uuid(), cycle.id, cycle.process_id, representation.id,
               CASE WHEN snapshot.is_complete THEN 'established' ELSE 'pending' END,
               CASE WHEN snapshot.is_complete THEN snapshot.version_id ELSE NULL END,
               CASE WHEN snapshot.is_complete THEN snapshot.id ELSE NULL END,
               CASE WHEN snapshot.is_complete THEN snapshot.normalizer_version ELSE NULL END,
               CASE WHEN snapshot.is_complete THEN snapshot.processed_at ELSE NULL END
        FROM process_watch_cycles AS cycle
        JOIN representations AS representation
          ON representation.process_id = cycle.process_id
        LEFT JOIN LATERAL (
            SELECT movement_snapshot.*
            FROM movement_snapshots AS movement_snapshot
            WHERE movement_snapshot.version_id = representation.latest_version_id
            ORDER BY movement_snapshot.processed_at DESC, movement_snapshot.id DESC
            LIMIT 1
        ) AS snapshot ON TRUE
        """
    )


def downgrade() -> None:
    op.drop_index("ix_process_news_process_feed", table_name="process_news")
    op.drop_index("ix_process_news_category_feed", table_name="process_news")
    op.drop_index("ix_process_news_status_feed", table_name="process_news")
    op.drop_index("ix_process_news_feed", table_name="process_news")
    op.drop_table("process_news")
    op.drop_index(
        "ix_representation_watch_baselines_cycle_state",
        table_name="representation_watch_baselines",
    )
    op.drop_table("representation_watch_baselines")
    op.drop_index("ix_process_watch_cycles_process_number", table_name="process_watch_cycles")
    op.drop_index("uq_process_watch_cycles_active_process", table_name="process_watch_cycles")
    op.drop_table("process_watch_cycles")
    op.drop_constraint("uq_representations_id_process", "representations", type_="unique")
