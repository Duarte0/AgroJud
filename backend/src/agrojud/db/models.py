"""SQLAlchemy models for the locally persisted source covers."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata for domain persistence models."""


class Process(Base):
    """One process grouped only by its exact, textual CNJ number."""

    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint("numero_cnj", name="uq_processes_numero_cnj"),
        CheckConstraint("numero_cnj ~ '^[0-9]{20}$'", name="ck_processes_numero_cnj"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    numero_cnj: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Representation(Base):
    """A source-specific record for one tribunal and source identifier."""

    __tablename__ = "representations"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "tribunal",
            "source_id",
            name="uq_representations_source_tribunal_source_id",
        ),
        CheckConstraint("length(btrim(source)) > 0", name="ck_representations_source_nonempty"),
        CheckConstraint("length(btrim(tribunal)) > 0", name="ck_representations_tribunal_nonempty"),
        CheckConstraint(
            "length(btrim(source_id)) > 0", name="ck_representations_source_id_nonempty"
        ),
        CheckConstraint(
            "not source_filed_at_timezone_ambiguous or "
            "(source_filed_at is null and source_filed_at_original is not null)",
            name="ck_representations_filed_date_ambiguity",
        ),
        CheckConstraint(
            "not source_updated_at_timezone_ambiguous or "
            "(source_updated_at is null and source_updated_at_original is not null)",
            name="ck_representations_updated_date_ambiguity",
        ),
        ForeignKeyConstraint(
            ["id", "latest_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_representations_latest_version",
            use_alter=True,
        ),
        Index("ix_representations_classe_codigo", "class_code"),
        Index("ix_representations_orgao_codigo", "court_unit_code"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("processes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    tribunal: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    latest_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    class_code: Mapped[str | None] = mapped_column(String(80))
    class_name: Mapped[str | None] = mapped_column(Text)
    grau: Mapped[str | None] = mapped_column(String(80))
    court_unit_code: Mapped[str | None] = mapped_column(String(80))
    court_unit_name: Mapped[str | None] = mapped_column(Text)
    source_filed_at_original: Mapped[str | None] = mapped_column(Text)
    source_filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_filed_at_timezone_ambiguous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source_updated_at_original: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at_timezone_ambiguous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Collection(Base):
    """A local collection identity independent of the future job queue."""

    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("mode in ('demo', 'real')", name="ck_collections_mode"),
        CheckConstraint(
            "length(btrim(criteria_sha256)) = 64", name="ck_collections_criteria_hash_length"
        ),
        CheckConstraint(
            "criteria_sha256 ~ '^[0-9a-f]{64}$'", name="ck_collections_criteria_hash_hex"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    resolved_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    criteria_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    previous_collection_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("collections.id", ondelete="RESTRICT")
    )


class RepresentationVersion(Base):
    """An immutable raw JSONB source payload, content-addressed per representation."""

    __tablename__ = "representation_versions"
    __table_args__ = (
        UniqueConstraint(
            "representation_id", "payload_sha256", name="uq_representation_versions_hash"
        ),
        UniqueConstraint(
            "representation_id", "id", name="uq_representation_versions_representation_id_id"
        ),
        CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_representation_versions_hash_hex"
        ),
        CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_representation_versions_normalizer_nonempty",
        ),
        CheckConstraint(
            "not source_filed_at_timezone_ambiguous or "
            "(source_filed_at is null and source_filed_at_original is not null)",
            name="ck_representation_versions_filed_date_ambiguity",
        ),
        CheckConstraint(
            "not source_updated_at_timezone_ambiguous or "
            "(source_updated_at is null and source_updated_at_original is not null)",
            name="ck_representation_versions_updated_date_ambiguity",
        ),
        Index("ix_representation_versions_first_observed", "first_observed_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    representation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("representations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_filed_at_original: Mapped[str | None] = mapped_column(Text)
    source_filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_filed_at_timezone_ambiguous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source_updated_at_original: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at_timezone_ambiguous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RepresentationSubject(Base):
    """Structured current subject values for filtering representations."""

    __tablename__ = "representation_subjects"
    __table_args__ = (
        UniqueConstraint("representation_id", "ordinal", name="uq_representation_subjects_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_representation_subjects_ordinal_positive"),
        CheckConstraint(
            "subject_code is not null or subject_name is not null",
            name="ck_representation_subjects_value_present",
        ),
        Index("ix_representation_subjects_code", "subject_code"),
        Index("ix_representation_subjects_name", "subject_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    representation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("representations.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_code: Mapped[str | None] = mapped_column(String(80))
    subject_name: Mapped[str | None] = mapped_column(Text)


class CollectionObservation(Base):
    """One hit position observed for a collection, linked to its immutable version."""

    __tablename__ = "collection_observations"
    __table_args__ = (
        UniqueConstraint(
            "collection_id", "page_key", "hit_ordinal", name="uq_observations_page_position"
        ),
        UniqueConstraint(
            "collection_id", "representation_id", "id", name="uq_observations_result_reference"
        ),
        CheckConstraint("length(btrim(page_key)) > 0", name="ck_observations_page_key_nonempty"),
        CheckConstraint("hit_ordinal >= 1", name="ck_observations_ordinal_positive"),
        CheckConstraint(
            "capture_outcome in ('new', 'updated', 'unchanged')",
            name="ck_observations_capture_outcome",
        ),
        ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_observations_representation_version",
        ),
        ForeignKeyConstraint(
            ["representation_id", "regressed_from_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_observations_regression_version",
        ),
        Index("ix_collection_observations_representation", "representation_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    page_key: Mapped[str] = mapped_column(Text, nullable=False)
    hit_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    source_filed_at_regressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    regressed_from_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))


class CollectionResult(Base):
    """One result per collection and representation, separate from hit observations."""

    __tablename__ = "collection_results"
    __table_args__ = (
        UniqueConstraint(
            "collection_id", "representation_id", name="uq_collection_results_representation"
        ),
        CheckConstraint(
            "capture_outcome in ('new', 'updated', 'unchanged')",
            name="ck_collection_results_capture_outcome",
        ),
        ForeignKeyConstraint(
            ["collection_id", "representation_id", "first_observation_id"],
            [
                "collection_observations.collection_id",
                "collection_observations.representation_id",
                "collection_observations.id",
            ],
            name="fk_collection_results_first_observation",
        ),
        Index("ix_collection_results_representation", "representation_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    representation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("representations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    first_observation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    included_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_outcome: Mapped[str] = mapped_column(String(12), nullable=False)
