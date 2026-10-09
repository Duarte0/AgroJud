"""SQLAlchemy models for the locally persisted source covers."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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


class ProcessWatchlistEntry(Base):
    """Current manual watch state for one locally known process."""

    __tablename__ = "process_watchlist_entries"
    __table_args__ = (
        CheckConstraint(
            "(active and removed_at is null) or (not active and removed_at is not null)",
            name="ck_process_watchlist_active_removed_at",
        ),
        CheckConstraint(
            "(active and next_run_at is not null) or (not active and next_run_at is null)",
            name="ck_process_watchlist_schedule_matches_active",
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_process_watchlist_process",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_process_watchlist_active_included",
            "included_at",
            "process_id",
            postgresql_where=text("active"),
        ),
    )

    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    included_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessWatchlistHistory(Base):
    """Append-only audit of effective manual watchlist transitions."""

    __tablename__ = "process_watchlist_history"
    __table_args__ = (
        CheckConstraint("action in ('included', 'removed')", name="ck_watchlist_history_action"),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_watchlist_history_process",
            ondelete="RESTRICT",
        ),
        Index("ix_watchlist_history_process_created", "process_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessWatchCycle(Base):
    """One active interval during which local observations may create news."""

    __tablename__ = "process_watch_cycles"
    __table_args__ = (
        UniqueConstraint("process_id", "cycle_number", name="uq_process_watch_cycles_number"),
        UniqueConstraint("id", "process_id", name="uq_process_watch_cycles_id_process"),
        CheckConstraint("cycle_number >= 1", name="ck_process_watch_cycles_number_positive"),
        CheckConstraint(
            "ended_at is null or ended_at >= started_at",
            name="ck_process_watch_cycles_end_after_start",
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["process_watchlist_entries.process_id"],
            name="fk_process_watch_cycles_watch_entry",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_process_watch_cycles_active_process",
            "process_id",
            unique=True,
            postgresql_where=text("ended_at is null"),
        ),
        Index("ix_process_watch_cycles_process_number", "process_id", "cycle_number"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepresentationWatchBaseline(Base):
    """Complete movement version chosen as the reference in one watch cycle."""

    __tablename__ = "representation_watch_baselines"
    __table_args__ = (
        UniqueConstraint(
            "watch_cycle_id",
            "representation_id",
            name="uq_representation_watch_baselines_cycle_representation",
        ),
        CheckConstraint(
            "state in ('pending', 'established')", name="ck_representation_watch_baselines_state"
        ),
        CheckConstraint(
            "(state = 'pending' and version_id is null and snapshot_id is null "
            "and normalizer_version is null and established_at is null) or "
            "(state = 'established' and version_id is not null and snapshot_id is not null "
            "and normalizer_version is not null and established_at is not null)",
            name="ck_representation_watch_baselines_state_fields",
        ),
        ForeignKeyConstraint(
            ["watch_cycle_id", "process_id"],
            ["process_watch_cycles.id", "process_watch_cycles.process_id"],
            name="fk_representation_watch_baselines_cycle_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["representation_id", "process_id"],
            ["representations.id", "representations.process_id"],
            name="fk_representation_watch_baselines_representation_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_representation_watch_baselines_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_representation_watch_baselines_snapshot",
            ondelete="RESTRICT",
        ),
        Index("ix_representation_watch_baselines_cycle_state", "watch_cycle_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    watch_cycle_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    normalizer_version: Mapped[str | None] = mapped_column(String(80))
    established_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessNews(Base):
    """Idempotent, reviewable evidence observed during a watch cycle."""

    __tablename__ = "process_news"
    __table_args__ = (
        UniqueConstraint(
            "process_id",
            "representation_id",
            "identity_key",
            "category",
            name="uq_process_news_identity_category",
        ),
        CheckConstraint(
            "category in ('NEW_OBSERVATION', 'ALTERATION_OBSERVED', 'NEW_REPRESENTATION')",
            name="ck_process_news_category",
        ),
        CheckConstraint("status in ('pending', 'reviewed')", name="ck_process_news_status"),
        CheckConstraint(
            "source_date_status in "
            "('unknown', 'missing', 'null', 'timezone_aware', 'timezone_ambiguous', 'unparseable')",
            name="ck_process_news_date_status",
        ),
        CheckConstraint(
            "provenance in ('ingestion', 'quarantine_reprocess')",
            name="ck_process_news_provenance",
        ),
        CheckConstraint(
            "length(btrim(identity_key)) > 0", name="ck_process_news_identity_nonempty"
        ),
        ForeignKeyConstraint(
            ["representation_id", "process_id"],
            ["representations.id", "representations.process_id"],
            name="fk_process_news_representation_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["watch_cycle_id", "process_id"],
            ["process_watch_cycles.id", "process_watch_cycles.process_id"],
            name="fk_process_news_cycle_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["occurrence_id", "representation_id"],
            ["movement_occurrences.id", "movement_occurrences.representation_id"],
            name="fk_process_news_occurrence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["representation_id", "origin_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_process_news_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_process_news_snapshot",
            ondelete="RESTRICT",
        ),
        Index("ix_process_news_feed", "first_observed_at", "id"),
        Index("ix_process_news_status_feed", "status", "first_observed_at", "id"),
        Index("ix_process_news_category_feed", "category", "first_observed_at", "id"),
        Index("ix_process_news_process_feed", "process_id", "first_observed_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    watch_cycle_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    origin_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    origin_snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_date_original: Mapped[Any | None] = mapped_column(JSONB)
    source_date_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessTriage(Base):
    """Latest human review state, stored separately from imported process data."""

    __tablename__ = "process_triage"
    __table_args__ = (
        CheckConstraint(
            "decision in ('pending', 'relevant', 'discarded')", name="ck_process_triage_decision"
        ),
        CheckConstraint(
            "rural_link in ('unconfirmed', 'confirmed')", name="ck_process_triage_rural_link"
        ),
        CheckConstraint("length(note) <= 5000", name="ck_process_triage_note_length"),
        CheckConstraint(
            "rural_link <> 'confirmed' or length(btrim(note)) > 0",
            name="ck_process_triage_confirmation_note",
        ),
        CheckConstraint("version >= 1", name="ck_process_triage_version_positive"),
        Index("ix_process_triage_decision", "decision", "process_id"),
        Index("ix_process_triage_rural_link", "rural_link", "process_id"),
    )

    process_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("processes.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    rural_link: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessTriageHistory(Base):
    """Append-only record of every effective human triage transition."""

    __tablename__ = "process_triage_history"
    __table_args__ = (
        UniqueConstraint("process_id", "version", name="uq_process_triage_history_version"),
        CheckConstraint("version >= 1", name="ck_process_triage_history_version_positive"),
        CheckConstraint("origin = 'manual'", name="ck_process_triage_history_origin_manual"),
        ForeignKeyConstraint(
            ["process_id"], ["processes.id"], ondelete="RESTRICT", name="fk_triage_history_process"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String(12), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Representation(Base):
    """A source-specific record for one tribunal and source identifier."""

    __tablename__ = "representations"
    __table_args__ = (
        UniqueConstraint("id", "process_id", name="uq_representations_id_process"),
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


class MovementOccurrence(Base):
    """One immutable movement identity and multiplicity for a representation."""

    __tablename__ = "movement_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "representation_id",
            "normalizer_version",
            "content_sha256",
            "multiplicity_ordinal",
            name="uq_movement_occurrences_identity",
        ),
        UniqueConstraint(
            "id", "representation_id", name="uq_movement_occurrences_id_representation"
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_movement_occurrences_hash_hex"
        ),
        CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_movement_occurrences_normalizer_nonempty",
        ),
        CheckConstraint(
            "multiplicity_ordinal >= 1", name="ck_movement_occurrences_ordinal_positive"
        ),
        CheckConstraint(
            "source_date_status in "
            "('missing', 'null', 'timezone_aware', 'timezone_ambiguous', 'unparseable')",
            name="ck_movement_occurrences_date_status",
        ),
        Index(
            "ix_movement_occurrences_representation_date",
            "representation_id",
            "source_date_normalized",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    representation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("representations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    normalizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    multiplicity_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_movement: Mapped[Any] = mapped_column(JSONB, nullable=False)
    normalized_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    auxiliary_key: Mapped[str | None] = mapped_column(Text)
    source_date_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_date_original: Mapped[Any | None] = mapped_column(JSONB)
    source_date_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_date_normalized: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MovementSnapshot(Base):
    """Completeness and normalization result for one payload version and algorithm."""

    __tablename__ = "movement_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "normalizer_version", name="uq_movement_snapshots_version_normalizer"
        ),
        UniqueConstraint("id", "representation_id", name="uq_movement_snapshots_id_representation"),
        CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_movement_snapshots_normalizer_nonempty",
        ),
        CheckConstraint(
            "rejection_count >= 0", name="ck_movement_snapshots_rejections_nonnegative"
        ),
        CheckConstraint(
            "is_complete = (rejection_count = 0)",
            name="ck_movement_snapshots_completeness_consistent",
        ),
        CheckConstraint("result_sha256 ~ '^[0-9a-f]{64}$'", name="ck_movement_snapshots_hash_hex"),
        ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_movement_snapshots_representation_version",
        ),
        Index("ix_movement_snapshots_representation", "representation_id", "processed_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MovementSnapshotOccurrence(Base):
    """Presence and comparison status of an occurrence for one payload snapshot."""

    __tablename__ = "movement_snapshot_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "occurrence_id", name="uq_movement_snapshot_occurrences_pair"
        ),
        CheckConstraint(
            "(present and comparison_result in "
            "('FIRST_OBSERVED', 'KNOWN', 'ALTERATION_OBSERVED')) or "
            "(not present and comparison_result = 'NOT_PRESENT_IN_SNAPSHOT')",
            name="ck_movement_snapshot_occurrences_result_presence",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_movement_snapshot_occurrences_snapshot_representation",
        ),
        ForeignKeyConstraint(
            ["occurrence_id", "representation_id"],
            ["movement_occurrences.id", "movement_occurrences.representation_id"],
            name="fk_movement_snapshot_occurrences_occurrence_representation",
        ),
        Index("ix_movement_snapshot_occurrences_occurrence", "occurrence_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comparison_result: Mapped[str] = mapped_column(String(32), nullable=False)
    comparison_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class QuarantineRejection(Base):
    """Immutable source hit and localized diagnostic awaiting local reprocessing."""

    __tablename__ = "quarantine_rejections"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "page_key",
            "hit_ordinal",
            "error_path",
            "validation_code",
            "normalizer_version",
            name="uq_quarantine_rejections_location_diagnostic",
        ),
        CheckConstraint("length(btrim(page_key)) > 0", name="ck_quarantine_page_key_nonempty"),
        CheckConstraint("hit_ordinal >= 1", name="ck_quarantine_hit_ordinal_positive"),
        CheckConstraint("length(btrim(error_path)) > 0", name="ck_quarantine_error_path_nonempty"),
        CheckConstraint(
            "length(btrim(validation_code)) > 0", name="ck_quarantine_validation_code_nonempty"
        ),
        CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_quarantine_normalizer_nonempty",
        ),
        CheckConstraint("status in ('pending', 'resolved')", name="ck_quarantine_status"),
        CheckConstraint(
            "(status = 'pending' and resolved_at is null) or "
            "(status = 'resolved' and resolved_at is not null)",
            name="ck_quarantine_resolution_status_consistent",
        ),
        Index("ix_quarantine_status_observed", "status", "first_observed_at"),
        Index("ix_quarantine_page_position", "collection_id", "page_key", "hit_ordinal"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    page_key: Mapped[str] = mapped_column(Text, nullable=False)
    hit_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    error_path: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hit: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation_code: Mapped[str] = mapped_column(String(80), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(8), nullable=False, default="pending", server_default="pending"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuarantineResolution(Base):
    """Append-only audit event for a quarantine item resolved by a normalizer."""

    __tablename__ = "quarantine_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "rejection_id", "normalizer_version", name="uq_quarantine_resolutions_normalizer"
        ),
        CheckConstraint(
            "length(btrim(normalizer_version)) > 0",
            name="ck_quarantine_resolutions_normalizer_nonempty",
        ),
        ForeignKeyConstraint(
            ["representation_id", "version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_quarantine_resolutions_representation_version",
        ),
        ForeignKeyConstraint(
            ["rejection_id"],
            ["quarantine_rejections.id"],
            name="fk_quarantine_resolutions_rejection",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    rejection_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    resolution_detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Job(Base):
    """Persisted work item with an expiring, token-guarded worker lease."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type in ('discovery', 'refresh_number', 'reprocess_rules')",
            name="ck_jobs_type",
        ),
        CheckConstraint("mode in ('demo', 'real')", name="ck_jobs_mode"),
        CheckConstraint(
            "status in ('queued', 'running', 'retry_wait', 'completed', 'partial', "
            "'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        CheckConstraint("length(btrim(source)) > 0", name="ck_jobs_source_nonempty"),
        CheckConstraint("length(btrim(tribunal)) > 0", name="ck_jobs_tribunal_nonempty"),
        CheckConstraint("operation_key ~ '^[0-9a-f]{64}$'", name="ck_jobs_operation_key_hex"),
        CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_nonnegative"),
        CheckConstraint("retry_cycle >= 1", name="ck_jobs_retry_cycle_positive"),
        CheckConstraint("page_attempt_count between 0 and 5", name="ck_jobs_page_attempt_count"),
        CheckConstraint(
            "persistence_attempt_count between 0 and 5",
            name="ck_jobs_persistence_attempt_count",
        ),
        CheckConstraint("recovery_count >= 0", name="ck_jobs_recovery_count_nonnegative"),
        CheckConstraint("event_count >= 0", name="ck_jobs_event_count_nonnegative"),
        CheckConstraint(
            "predecessor_job_id is null or predecessor_job_id <> id",
            name="ck_jobs_predecessor_not_self",
        ),
        CheckConstraint(
            "(status = 'running' and lease_token is not null and lease_owner is not null "
            "and lease_expires_at is not null and heartbeat_at is not null) or "
            "(status <> 'running' and lease_token is null and lease_owner is null "
            "and lease_expires_at is null and heartbeat_at is null)",
            name="ck_jobs_lease_matches_status",
        ),
        CheckConstraint(
            "status = 'running' or not cancel_requested", name="ck_jobs_cancel_only_running"
        ),
        ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_jobs_collection_id_collections",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["predecessor_job_id"],
            ["jobs.id"],
            name="fk_jobs_predecessor_job_id_jobs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("collection_id", name="uq_jobs_collection_id"),
        Index(
            "uq_jobs_active_operation_key",
            "operation_key",
            unique=True,
            postgresql_where=text("status in ('queued', 'running', 'retry_wait')"),
        ),
        Index(
            "ix_jobs_next_attempt_claim",
            "status",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status in ('queued', 'retry_wait')"),
        ),
        Index(
            "ix_jobs_expired_claim",
            "lease_expires_at",
            "created_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    collection_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    job_type: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    tribunal: Mapped[str] = mapped_column(String(40), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="queued", server_default="queued"
    )
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retry_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    page_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    persistence_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    recovery_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cursor_invalid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    predecessor_job_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedSearch(Base):
    """Current activation and schedule pointer for an immutable saved search."""

    __tablename__ = "saved_searches"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) between 1 and 160", name="ck_saved_searches_name"),
        CheckConstraint("current_version >= 1", name="ck_saved_searches_version_positive"),
        CheckConstraint(
            "(enabled and next_run_at is not null) or (not enabled and next_run_at is null)",
            name="ck_saved_searches_schedule_matches_enabled",
        ),
        Index(
            "ix_saved_searches_due",
            "next_run_at",
            "id",
            postgresql_where=text("enabled"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SavedSearchVersion(Base):
    """Append-only criteria and preset contract for one saved-search revision."""

    __tablename__ = "saved_search_versions"
    __table_args__ = (
        UniqueConstraint("saved_search_id", "version", name="uq_saved_search_versions_number"),
        UniqueConstraint("id", "saved_search_id", name="uq_saved_search_versions_id_search"),
        CheckConstraint("version >= 1", name="ck_saved_search_versions_positive"),
        CheckConstraint(
            "length(btrim(name)) between 1 and 160", name="ck_saved_search_versions_name"
        ),
        CheckConstraint(
            "window_mode in ('fixed', 'rolling_12_months')",
            name="ck_saved_search_versions_window_mode",
        ),
        ForeignKeyConstraint(
            ["saved_search_id"],
            ["saved_searches.id"],
            name="fk_saved_search_versions_search",
            ondelete="RESTRICT",
        ),
        Index("ix_saved_search_versions_search", "saved_search_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    saved_search_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    preset_id: Mapped[str] = mapped_column(String(120), nullable=False)
    preset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    window_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    catalog_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScheduleDispatch(Base):
    """Idempotent local-date dispatch and its pending recovery/queue state."""

    __tablename__ = "schedule_dispatches"
    __table_args__ = (
        CheckConstraint(
            "(target_kind = 'saved_search' and saved_search_id is not null "
            "and process_id is null) or "
            "(target_kind = 'watch' and process_id is not null "
            "and saved_search_id is null)",
            name="ck_schedule_dispatches_target",
        ),
        CheckConstraint(
            "status in ('pending', 'enqueued', 'blocked', 'cancelled', 'coalesced')",
            name="ck_schedule_dispatches_status",
        ),
        CheckConstraint(
            "(missed_from is null and missed_through is null) or "
            "(missed_from is not null and missed_through is not null "
            "and missed_from <= missed_through)",
            name="ck_schedule_dispatches_missed_interval",
        ),
        CheckConstraint(
            "(status = 'pending' and job_id is null and request_snapshot is not null) or "
            "(status = 'enqueued' and job_id is not null and request_snapshot is not null) or "
            "(status in ('blocked', 'cancelled', 'coalesced') and job_id is null)",
            name="ck_schedule_dispatches_status_fields",
        ),
        ForeignKeyConstraint(
            ["saved_search_id"],
            ["saved_searches.id"],
            name="fk_schedule_dispatches_saved_search",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_schedule_dispatches_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_schedule_dispatches_job", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["coalesced_into_id"],
            ["schedule_dispatches.id"],
            name="fk_schedule_dispatches_coalesced_into",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_schedule_dispatch_search_date",
            "saved_search_id",
            "scheduled_for_date",
            unique=True,
            postgresql_where=text("saved_search_id is not null"),
        ),
        Index(
            "uq_schedule_dispatch_watch_date",
            "process_id",
            "scheduled_for_date",
            unique=True,
            postgresql_where=text("process_id is not null"),
        ),
        Index(
            "uq_schedule_dispatch_pending_search",
            "saved_search_id",
            unique=True,
            postgresql_where=text("status = 'pending' and saved_search_id is not null"),
        ),
        Index(
            "uq_schedule_dispatch_pending_watch",
            "process_id",
            unique=True,
            postgresql_where=text("status = 'pending' and process_id is not null"),
        ),
        Index("ix_schedule_dispatch_search_date", "saved_search_id", "scheduled_for_date"),
        Index("ix_schedule_dispatch_watch_date", "process_id", "scheduled_for_date"),
        Index("ix_schedule_dispatch_pending", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    saved_search_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    process_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    scheduled_for_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    missed_from: Mapped[date | None] = mapped_column(Date)
    missed_through: Mapped[date | None] = mapped_column(Date)
    coalesced_into_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QueueClaimState(Base):
    """Persist the last claimed refresh/discovery category for fair worker turns."""

    __tablename__ = "queue_claim_state"
    __table_args__ = (
        CheckConstraint(
            "last_category in ('refresh', 'non_refresh')",
            name="ck_queue_claim_state_category",
        ),
    )

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_category: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceRateLimit(Base):
    """Persistent request spacing and server cooldown shared by jobs per source."""

    __tablename__ = "source_rate_limits"
    __table_args__ = (
        CheckConstraint("length(btrim(source)) > 0", name="ck_source_rate_limits_source_nonempty"),
    )

    source: Mapped[str] = mapped_column(String(80), primary_key=True)
    next_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobAttempt(Base):
    """One durable ownership period, including abandoned and recovered runs."""

    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
        CheckConstraint("attempt_number >= 1", name="ck_job_attempts_number_positive"),
        CheckConstraint(
            "(finished_at is null and outcome is null) or "
            "(finished_at is not null and outcome in "
            "('completed', 'partial', 'failed', 'cancelled', 'retry_wait', 'lease_expired'))",
            name="ck_job_attempts_outcome_consistent",
        ),
        CheckConstraint(
            "error_code is null or length(btrim(error_code)) > 0",
            name="ck_job_attempts_error_code_nonempty",
        ),
        CheckConstraint(
            "error_summary is null or length(btrim(error_summary)) > 0",
            name="ck_job_attempts_error_summary_nonempty",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_attempts_job_id_jobs", ondelete="RESTRICT"
        ),
        Index(
            "uq_job_attempts_active_job",
            "job_id",
            unique=True,
            postgresql_where=text("finished_at is null"),
        ),
        Index("ix_job_attempts_job_started", "job_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(String(240))


class JobEvent(Base):
    """Append-only, sanitized lifecycle event for a persisted job."""

    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "event_number", name="uq_job_events_number"),
        CheckConstraint("length(btrim(event_type)) > 0", name="ck_job_events_type_nonempty"),
        CheckConstraint("event_number >= 1", name="ck_job_events_number_positive"),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_events_job_id_jobs", ondelete="RESTRICT"
        ),
        Index("ix_job_events_job_created", "job_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    event_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobCheckpoint(Base):
    """Initial and compare-and-swap progress checkpoint owned by one job."""

    __tablename__ = "job_checkpoints"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_checkpoints_job_id"),
        CheckConstraint("next_page >= 1", name="ck_job_checkpoints_next_page_positive"),
        CheckConstraint("revision >= 0", name="ck_job_checkpoints_revision_nonnegative"),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_checkpoints_job_id_jobs",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    cursor: Mapped[Any | None] = mapped_column(JSONB)
    next_page: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SignalRunProcess(Base):
    """Publication boundary and progress for one process in a local rule run."""

    __tablename__ = "signal_run_processes"
    __table_args__ = (
        UniqueConstraint("job_id", "process_id", name="uq_signal_run_processes_pair"),
        CheckConstraint(
            "status in ('pending', 'completed', 'stale', 'not_evaluated')",
            name="ck_signal_run_processes_status",
        ),
        CheckConstraint("input_count >= 0", name="ck_signal_run_processes_input_count"),
        CheckConstraint(
            "processed_input_count between 0 and input_count",
            name="ck_signal_run_processes_processed_count",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_run_processes_job", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_run_processes_process",
            ondelete="RESTRICT",
        ),
        Index("ix_signal_run_processes_process", "process_id", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    input_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignalRunInput(Base):
    """Immutable representation/version snapshot plus its local processing state."""

    __tablename__ = "signal_run_inputs"
    __table_args__ = (
        UniqueConstraint("job_id", "representation_id", name="uq_signal_run_inputs_representation"),
        CheckConstraint(
            "status in ('pending', 'completed', 'not_evaluated')",
            name="ck_signal_run_inputs_status",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_run_inputs_job", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_run_inputs_process",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_signal_run_inputs_representation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["representation_id", "latest_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_signal_run_inputs_latest_version",
        ),
        ForeignKeyConstraint(
            ["representation_id", "input_version_id"],
            ["representation_versions.representation_id", "representation_versions.id"],
            name="fk_signal_run_inputs_input_version",
        ),
        ForeignKeyConstraint(
            ["movement_snapshot_id", "representation_id"],
            ["movement_snapshots.id", "movement_snapshots.representation_id"],
            name="fk_signal_run_inputs_snapshot_representation",
        ),
        Index("ix_signal_run_inputs_job_cursor", "job_id", "id"),
        Index("ix_signal_run_inputs_process", "job_id", "process_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    latest_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    input_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    movement_snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    input_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    diagnostic: Mapped[str | None] = mapped_column(Text)


class ProcessSignal(Base):
    """One deterministic, historical signal linked to a normalized occurrence."""

    __tablename__ = "process_signals"
    __table_args__ = (
        UniqueConstraint(
            "rule_id",
            "rule_version",
            "evidence_id",
            "result_fingerprint",
            name="uq_process_signals_rule_evidence_result",
        ),
        CheckConstraint(
            "evidence_kind in ('movement_occurrence', 'process_attribute', "
            "'representation_attribute')",
            name="ck_process_signals_evidence_kind",
        ),
        CheckConstraint(
            "evidence_kind <> 'movement_occurrence' or movement_occurrence_id = evidence_id",
            name="ck_process_signals_movement_evidence_id",
        ),
        CheckConstraint("environment in ('demo', 'real')", name="ck_process_signals_environment"),
        CheckConstraint(
            "result_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_process_signals_fingerprint_hex"
        ),
        CheckConstraint("length(btrim(rule_id)) > 0", name="ck_process_signals_rule_nonempty"),
        CheckConstraint(
            "length(btrim(rule_version)) > 0", name="ck_process_signals_rule_version_nonempty"
        ),
        ForeignKeyConstraint(
            ["process_id"], ["processes.id"], name="fk_process_signals_process", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["representation_id"],
            ["representations.id"],
            name="fk_process_signals_representation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["movement_occurrence_id"],
            ["movement_occurrences.id"],
            name="fk_process_signals_occurrence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_run_id"],
            ["jobs.id"],
            name="fk_process_signals_created_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["published_run_id"],
            ["jobs.id"],
            name="fk_process_signals_published_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_version_id"],
            ["representation_versions.id"],
            name="fk_process_signals_evidence_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_snapshot_id"],
            ["movement_snapshots.id"],
            name="fk_process_signals_evidence_snapshot",
            ondelete="RESTRICT",
        ),
        Index("ix_process_signals_process_current", "process_id", "is_current", "category"),
        Index("ix_process_signals_rule_version", "rule_id", "rule_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    representation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    movement_occurrence_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    enablement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    published_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalEvaluation(Base):
    """Append-only per-rule audit entry, including negative and insufficient checks."""

    __tablename__ = "signal_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "input_id",
            "rule_id",
            "rule_version",
            name="uq_signal_evaluations_run_input_rule",
        ),
        CheckConstraint(
            "outcome in ('matched', 'no_match', 'not_evaluated')",
            name="ck_signal_evaluations_outcome",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_signal_evaluations_job", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["input_id"],
            ["signal_run_inputs.id"],
            name="fk_signal_evaluations_input",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_signal_evaluations_process",
            ondelete="RESTRICT",
        ),
        Index("ix_signal_evaluations_process_rule", "process_id", "rule_id", "evaluated_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    input_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    process_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    diagnostic: Mapped[str | None] = mapped_column(Text)
    matched_signal_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_version_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    movement_snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
