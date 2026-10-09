"""Persist versioned movement snapshots, occurrences, and rejections."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0003"
down_revision: str | Sequence[str] | None = "20261009_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "movement_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("multiplicity_ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_movement", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("auxiliary_key", sa.Text(), nullable=True),
        sa.Column("source_date_present", sa.Boolean(), nullable=False),
        sa.Column("source_date_original", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_date_status", sa.String(length=24), nullable=False),
        sa.Column("source_date_normalized", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_movement_occurrences_hash_hex"
        ),
        sa.CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_movement_occurrences_normalizer_nonempty",
        ),
        sa.CheckConstraint(
            "multiplicity_ordinal >= 1", name="ck_movement_occurrences_ordinal_positive"
        ),
        sa.CheckConstraint(
            "source_date_status in "
            "('missing', 'null', 'timezone_aware', 'timezone_ambiguous', 'unparseable')",
            name="ck_movement_occurrences_date_status",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_movement_occurrences_representation_id_representations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_movement_occurrences"),
        sa.UniqueConstraint(
            "id", "representation_id", name="uq_movement_occurrences_id_representation"
        ),
        sa.UniqueConstraint(
            "representation_id",
            "normalizer_version",
            "content_sha256",
            "multiplicity_ordinal",
            name="uq_movement_occurrences_identity",
        ),
    )
    op.create_index(
        "ix_movement_occurrences_representation_date",
        "movement_occurrences",
        ["representation_id", "source_date_normalized"],
        unique=False,
    )

    op.create_table(
        "movement_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("rejection_count", sa.Integer(), nullable=False),
        sa.Column("result_sha256", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_movement_snapshots_normalizer_nonempty",
        ),
        sa.CheckConstraint(
            "rejection_count >= 0", name="ck_movement_snapshots_rejections_nonnegative"
        ),
        sa.CheckConstraint(
            "is_complete = (rejection_count = 0)",
            name="ck_movement_snapshots_completeness_consistent",
        ),
        sa.CheckConstraint(
            "result_sha256 ~ '^[0-9a-f]{64}$'", name="ck_movement_snapshots_hash_hex"
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_movement_snapshots_representation_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_movement_snapshots"),
        sa.UniqueConstraint(
            "id", "representation_id", name="uq_movement_snapshots_id_representation"
        ),
        sa.UniqueConstraint(
            "version_id", "normalizer_version", name="uq_movement_snapshots_version_normalizer"
        ),
    )
    op.create_index(
        "ix_movement_snapshots_representation",
        "movement_snapshots",
        ["representation_id", "processed_at"],
        unique=False,
    )

    op.create_table(
        "movement_snapshot_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("comparison_result", sa.String(length=32), nullable=False),
        sa.Column("comparison_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "(present and comparison_result in "
            "('FIRST_OBSERVED', 'KNOWN', 'ALTERATION_OBSERVED')) or "
            "(not present and comparison_result = 'NOT_PRESENT_IN_SNAPSHOT')",
            name="ck_movement_snapshot_occurrences_result_presence",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_movement_snapshot_occurrences_snapshot_representation",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "representation_id"],
            ["movement_occurrences.id", "movement_occurrences.representation_id"],
            name="fk_movement_snapshot_occurrences_occurrence_representation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_movement_snapshot_occurrences"),
        sa.UniqueConstraint(
            "snapshot_id", "occurrence_id", name="uq_movement_snapshot_occurrences_pair"
        ),
    )
    op.create_index(
        "ix_movement_snapshot_occurrences_occurrence",
        "movement_snapshot_occurrences",
        ["occurrence_id"],
        unique=False,
    )

    op.create_table(
        "quarantine_rejections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_key", sa.Text(), nullable=False),
        sa.Column("hit_ordinal", sa.Integer(), nullable=False),
        sa.Column("error_path", sa.Text(), nullable=False),
        sa.Column("raw_hit", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_code", sa.String(length=80), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="pending", nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(btrim(page_key)) > 0", name="ck_quarantine_page_key_nonempty"),
        sa.CheckConstraint("hit_ordinal >= 1", name="ck_quarantine_hit_ordinal_positive"),
        sa.CheckConstraint(
            "length(btrim(error_path)) > 0", name="ck_quarantine_error_path_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(validation_code)) > 0",
            name="ck_quarantine_validation_code_nonempty",
        ),
        sa.CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_quarantine_normalizer_nonempty",
        ),
        sa.CheckConstraint("status in ('pending', 'resolved')", name="ck_quarantine_status"),
        sa.CheckConstraint(
            "(status = 'pending' and resolved_at is null) or "
            "(status = 'resolved' and resolved_at is not null)",
            name="ck_quarantine_resolution_status_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_quarantine_rejections_collection_id_collections",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quarantine_rejections"),
        sa.UniqueConstraint(
            "collection_id",
            "page_key",
            "hit_ordinal",
            "error_path",
            "validation_code",
            "normalizer_version",
            name="uq_quarantine_rejections_location_diagnostic",
        ),
    )
    op.create_index(
        "ix_quarantine_status_observed",
        "quarantine_rejections",
        ["status", "first_observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_quarantine_page_position",
        "quarantine_rejections",
        ["collection_id", "page_key", "hit_ordinal"],
        unique=False,
    )

    op.create_table(
        "quarantine_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rejection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resolution_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_quarantine_resolutions_normalizer_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_quarantine_resolutions_representation_version",
        ),
        sa.ForeignKeyConstraint(
            ["rejection_id"],
            ["quarantine_rejections.id"],
            name="fk_quarantine_resolutions_rejection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quarantine_resolutions"),
        sa.UniqueConstraint(
            "rejection_id", "normalizer_version", name="uq_quarantine_resolutions_normalizer"
        ),
    )


def downgrade() -> None:
    op.drop_table("quarantine_resolutions")
    op.drop_index("ix_quarantine_page_position", table_name="quarantine_rejections")
    op.drop_index("ix_quarantine_status_observed", table_name="quarantine_rejections")
    op.drop_table("quarantine_rejections")
    op.drop_index(
        "ix_movement_snapshot_occurrences_occurrence", table_name="movement_snapshot_occurrences"
    )
    op.drop_table("movement_snapshot_occurrences")
    op.drop_index("ix_movement_snapshots_representation", table_name="movement_snapshots")
    op.drop_table("movement_snapshots")
    op.drop_index("ix_movement_occurrences_representation_date", table_name="movement_occurrences")
    op.drop_table("movement_occurrences")
