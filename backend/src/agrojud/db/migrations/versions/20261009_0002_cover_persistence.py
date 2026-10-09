"""Persist source covers, versions, collection observations, and results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0002"
down_revision: str | Sequence[str] | None = "20261008_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("numero_cnj", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("numero_cnj ~ '^[0-9]{20}$'", name="ck_processes_numero_cnj"),
        sa.PrimaryKeyConstraint("id", name="pk_processes"),
        sa.UniqueConstraint("numero_cnj", name="uq_processes_numero_cnj"),
    )

    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("resolved_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("criteria_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("previous_collection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("mode in ('demo', 'real')", name="ck_collections_mode"),
        sa.CheckConstraint(
            "length(btrim(criteria_sha256)) = 64", name="ck_collections_criteria_hash_length"
        ),
        sa.CheckConstraint(
            "criteria_sha256 ~ '^[0-9a-f]{64}$'", name="ck_collections_criteria_hash_hex"
        ),
        sa.ForeignKeyConstraint(
            ["previous_collection_id"],
            ["collections.id"],
            name="fk_collections_previous_collection_id_collections",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collections"),
    )

    op.create_table(
        "representations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("tribunal", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("latest_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("class_code", sa.String(length=80), nullable=True),
        sa.Column("class_name", sa.Text(), nullable=True),
        sa.Column("grau", sa.String(length=80), nullable=True),
        sa.Column("court_unit_code", sa.String(length=80), nullable=True),
        sa.Column("court_unit_name", sa.Text(), nullable=True),
        sa.Column("source_filed_at_original", sa.Text(), nullable=True),
        sa.Column("source_filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_filed_at_timezone_ambiguous",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("source_updated_at_original", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_updated_at_timezone_ambiguous",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(source)) > 0", name="ck_representations_source_nonempty"),
        sa.CheckConstraint(
            "length(btrim(tribunal)) > 0", name="ck_representations_tribunal_nonempty"
        ),
        sa.CheckConstraint(
            "length(btrim(source_id)) > 0", name="ck_representations_source_id_nonempty"
        ),
        sa.CheckConstraint(
            "not source_filed_at_timezone_ambiguous or "
            "(source_filed_at is null and source_filed_at_original is not null)",
            name="ck_representations_filed_date_ambiguity",
        ),
        sa.CheckConstraint(
            "not source_updated_at_timezone_ambiguous or "
            "(source_updated_at is null and source_updated_at_original is not null)",
            name="ck_representations_updated_date_ambiguity",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_representations_process_id_processes",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_representations"),
        sa.UniqueConstraint(
            "source", "tribunal", "source_id", name="uq_representations_source_tribunal_source_id"
        ),
    )
    op.create_index(
        "ix_representations_classe_codigo", "representations", ["class_code"], unique=False
    )
    op.create_index(
        "ix_representations_orgao_codigo", "representations", ["court_unit_code"], unique=False
    )

    op.create_table(
        "representation_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("source_filed_at_original", sa.Text(), nullable=True),
        sa.Column("source_filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_filed_at_timezone_ambiguous",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("source_updated_at_original", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_updated_at_timezone_ambiguous",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_representation_versions_hash_hex",
        ),
        sa.CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_representation_versions_normalizer_nonempty",
        ),
        sa.CheckConstraint(
            "not source_filed_at_timezone_ambiguous or "
            "(source_filed_at is null and source_filed_at_original is not null)",
            name="ck_representation_versions_filed_date_ambiguity",
        ),
        sa.CheckConstraint(
            "not source_updated_at_timezone_ambiguous or "
            "(source_updated_at is null and source_updated_at_original is not null)",
            name="ck_representation_versions_updated_date_ambiguity",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_representation_versions_representation_id_representations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_representation_versions"),
        sa.UniqueConstraint(
            "representation_id", "id", name="uq_representation_versions_representation_id_id"
        ),
        sa.UniqueConstraint(
            "representation_id", "payload_sha256", name="uq_representation_versions_hash"
        ),
    )
    op.create_index(
        "ix_representation_versions_first_observed",
        "representation_versions",
        ["first_observed_at"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_representations_latest_version",
        "representations",
        "representation_versions",
        ["id", "latest_version_id"],
        ["representation_id", "id"],
    )

    op.create_table(
        "representation_subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("subject_code", sa.String(length=80), nullable=True),
        sa.Column("subject_name", sa.Text(), nullable=True),
        sa.CheckConstraint("ordinal >= 1", name="ck_representation_subjects_ordinal_positive"),
        sa.CheckConstraint(
            "subject_code is not null or subject_name is not null",
            name="ck_representation_subjects_value_present",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_representation_subjects_representation_id_representations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_representation_subjects"),
        sa.UniqueConstraint(
            "representation_id", "ordinal", name="uq_representation_subjects_ordinal"
        ),
    )
    op.create_index(
        "ix_representation_subjects_code", "representation_subjects", ["subject_code"], unique=False
    )
    op.create_index(
        "ix_representation_subjects_name", "representation_subjects", ["subject_name"], unique=False
    )

    op.create_table(
        "collection_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_key", sa.Text(), nullable=False),
        sa.Column("hit_ordinal", sa.Integer(), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capture_outcome", sa.String(length=12), nullable=False),
        sa.Column(
            "source_filed_at_regressed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("regressed_from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("length(btrim(page_key)) > 0", name="ck_observations_page_key_nonempty"),
        sa.CheckConstraint("hit_ordinal >= 1", name="ck_observations_ordinal_positive"),
        sa.CheckConstraint(
            "capture_outcome in ('new', 'updated', 'unchanged')",
            name="ck_observations_capture_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_collection_observations_collection_id_collections",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_observations_representation_version",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id", "regressed_from_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_observations_regression_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_observations"),
        sa.UniqueConstraint(
            "collection_id", "page_key", "hit_ordinal", name="uq_observations_page_position"
        ),
        sa.UniqueConstraint(
            "collection_id",
            "representation_id",
            "id",
            name="uq_observations_result_reference",
        ),
    )
    op.create_index(
        "ix_collection_observations_representation",
        "collection_observations",
        ["representation_id"],
        unique=False,
    )

    op.create_table(
        "collection_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("representation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("included_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capture_outcome", sa.String(length=12), nullable=False),
        sa.CheckConstraint(
            "capture_outcome in ('new', 'updated', 'unchanged')",
            name="ck_collection_results_capture_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_collection_results_collection_id_collections",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_collection_results_representation_id_representations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id", "representation_id", "first_observation_id"],
            [
                "collection_observations.collection_id",
                "collection_observations.representation_id",
                "collection_observations.id",
            ],
            name="fk_collection_results_first_observation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_results"),
        sa.UniqueConstraint(
            "collection_id", "representation_id", name="uq_collection_results_representation"
        ),
    )
    op.create_index(
        "ix_collection_results_representation",
        "collection_results",
        ["representation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_collection_results_representation", table_name="collection_results")
    op.drop_table("collection_results")
    op.drop_index("ix_collection_observations_representation", table_name="collection_observations")
    op.drop_table("collection_observations")
    op.drop_index("ix_representation_subjects_name", table_name="representation_subjects")
    op.drop_index("ix_representation_subjects_code", table_name="representation_subjects")
    op.drop_table("representation_subjects")
    op.drop_constraint("fk_representations_latest_version", "representations", type_="foreignkey")
    op.drop_index("ix_representation_versions_first_observed", table_name="representation_versions")
    op.drop_table("representation_versions")
    op.drop_index("ix_representations_orgao_codigo", table_name="representations")
    op.drop_index("ix_representations_classe_codigo", table_name="representations")
    op.drop_table("representations")
    op.drop_table("collections")
    op.drop_table("processes")
