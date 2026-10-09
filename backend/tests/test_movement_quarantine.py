"""PostgreSQL evidence for movement persistence and quarantine reprocessing."""

from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Base,
    CollectionObservation,
    CollectionResult,
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    Process,
    QuarantineRejection,
    QuarantineResolution,
    Representation,
    RepresentationSubject,
    RepresentationVersion,
)
from agrojud.db.movement_repositories import QuarantineRepository
from agrojud.domain.canonical_json import JSONValue
from agrojud.domain.movement_normalization import NORMALIZER_VERSION, MovementNormalizer
from agrojud.domain.occurrence_identity import normalize_movement
from agrojud.services.ingestion import create_collection, ingest_page
from agrojud.services.movement_ingestion import persist_movement_snapshot
from agrojud.services.quarantine_reprocessing import reprocess_quarantine_ids
from agrojud.sources.contracts import SourceHit, SourcePage

OBSERVED_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
CASE_A = "00000010020248090001"

EXPECTED_TABLES = {
    "alembic_version",
    "collection_observations",
    "collection_results",
    "collections",
    "job_attempts",
    "job_checkpoints",
    "job_events",
    "jobs",
    "movement_occurrences",
    "movement_snapshot_occurrences",
    "movement_snapshots",
    "processes",
    "quarantine_rejections",
    "quarantine_resolutions",
    "representation_subjects",
    "representation_versions",
    "representations",
}


@pytest.fixture
def migrated_engine(scratch_database_url: object) -> Iterator[Engine]:
    engine = make_engine(cast(str, scratch_database_url.render_as_string(hide_password=False)))
    with engine.begin() as connection:
        upgrade_database(connection)
    yield engine
    engine.dispose()


def make_movement(
    code: int, name: str, *, date_value: JSONValue = "20261008120000"
) -> dict[str, JSONValue]:
    return {
        "codigo": code,
        "dataHora": date_value,
        "nome": name,
        "orgaoJulgador": {"codigo": 123, "nome": "Vara sintética"},
        "complementosTabelados": [{"codigo": 1, "valor": "fixture"}],
    }


def make_hit(
    *,
    source_id: str = "fixture-origin-a",
    movements: JSONValue = None,
    include_movements: bool = True,
    case_number: str = CASE_A,
    sort_value: int = 1,
) -> SourceHit:
    source: dict[str, JSONValue] = {
        "id": source_id,
        "numeroProcesso": case_number,
        "tribunal": "TJGO",
        "dataAjuizamento": "20260110120000",
        "@timestamp": "2026-01-11T12:00:00Z",
        "grau": "G1",
        "classe": {"codigo": 1116, "nome": "Execução sintética"},
        "assuntos": [{"codigo": 4968, "nome": "Crédito rural sintético"}],
        "orgaoJulgador": {"codigo": 123, "nome": "Vara sintética"},
    }
    if include_movements:
        source["movimentos"] = movements
    return SourceHit(
        source_id=source_id,
        source=source,
        sort_values=(sort_value,),
        raw={"_id": source_id, "_source": source, "sort": [sort_value]},
    )


def make_page(*hits: SourceHit, responded_at: datetime = OBSERVED_AT) -> SourcePage:
    return SourcePage(
        hits=hits,
        total_value=len(hits),
        total_relation="eq",
        cursor_final=hits[-1].sort_values if hits else None,
        responded_at=responded_at,
    )


def new_collection(session: Session) -> UUID:
    collection = create_collection(
        session,
        mode="demo",
        resolved_criteria={"preset": "synthetic-movements-v1"},
    )
    return collection.id


def count_rows(session: Session, model: type[Base]) -> int:
    return cast(int, session.scalar(select(func.count()).select_from(model)) or 0)


def test_migration_applies_empty_and_from_previous_revision(scratch_database_url: object) -> None:
    database_url = cast(str, scratch_database_url.render_as_string(hide_password=False))
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            assert inspect(connection).get_table_names() == []
            from alembic import command

            from agrojud.db.migrations_runner import make_alembic_config

            command.upgrade(make_alembic_config(connection), "20261009_0002")
            assert "representation_versions" in inspect(connection).get_table_names()
            upgrade_database(connection)
            assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_movement_identity_preserves_multiplicity_per_representation_and_reordering(
    migrated_engine: Engine,
) -> None:
    movement_a = make_movement(85, "Citação sintética")
    movement_b = make_movement(246, "Conclusão sintética")
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        result = ingest_page(
            session,
            collection_id=collection_id,
            page_key="first",
            source="synthetic",
            page=make_page(
                make_hit(movements=[movement_a, movement_b], sort_value=1),
                make_hit(movements=[movement_b, movement_a], sort_value=2),
                make_hit(
                    source_id="fixture-origin-b",
                    movements=[movement_a, movement_b],
                    sort_value=3,
                ),
            ),
        )

        occurrences = session.scalars(select(MovementOccurrence)).all()
        snapshots = session.scalars(select(MovementSnapshot)).all()
        snapshot_links = session.scalars(select(MovementSnapshotOccurrence)).all()
        representations = session.scalars(select(Representation)).all()

        assert result.counts.new == 2
        assert len(representations) == 2
        assert len(occurrences) == 4
        assert len(snapshots) == 3
        assert len(snapshot_links) == 6
        assert len({item.representation_id for item in occurrences}) == 2
        first_representation = next(
            item for item in representations if item.source_id == "fixture-origin-a"
        )
        first_versions = session.scalars(
            select(RepresentationVersion).where(
                RepresentationVersion.representation_id == first_representation.id
            )
        ).all()
        assert len(first_versions) == 2
        links_by_version = {
            snapshot.version_id: session.scalars(
                select(MovementSnapshotOccurrence).where(
                    MovementSnapshotOccurrence.snapshot_id == snapshot.id
                )
            ).all()
            for snapshot in snapshots
        }
        first_a = next(item for item in snapshots if item.version_id == first_versions[0].id)
        second_a = next(item for item in snapshots if item.version_id == first_versions[1].id)
        assert {item.comparison_result for item in links_by_version[first_a.version_id]} == {
            "FIRST_OBSERVED"
        }
        assert {item.comparison_result for item in links_by_version[second_a.version_id]} == {
            "KNOWN"
        }
        assert all(item.present for item in links_by_version[second_a.version_id])
        assert {item.first_observed_at for item in occurrences} == {OBSERVED_AT}
        assert all(item.source_date_present for item in occurrences)
        assert {item.source_date_original for item in occurrences} == {"20261008120000"}
        assert {item.source_date_status for item in occurrences} == {"timezone_ambiguous"}
        assert all(item.source_date_normalized is None for item in occurrences)


def test_changed_description_is_persisted_as_an_observed_alteration(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        first = ingest_page(
            session,
            collection_id=collection_id,
            page_key="description-v1",
            source="synthetic",
            page=make_page(make_hit(movements=[make_movement(85, "Citação original")])),
        )
        second = ingest_page(
            session,
            collection_id=collection_id,
            page_key="description-v2",
            source="synthetic",
            page=make_page(
                make_hit(
                    movements=[make_movement(85, "Citação corrigida")],
                    sort_value=2,
                )
            ),
        )

        first_snapshot = session.scalar(
            select(MovementSnapshot).where(
                MovementSnapshot.version_id == first.versions[0].version_id
            )
        )
        second_snapshot = session.scalar(
            select(MovementSnapshot).where(
                MovementSnapshot.version_id == second.versions[0].version_id
            )
        )
        assert first_snapshot is not None and second_snapshot is not None
        first_link = session.scalar(
            select(MovementSnapshotOccurrence).where(
                MovementSnapshotOccurrence.snapshot_id == first_snapshot.id
            )
        )
        second_link = session.scalar(
            select(MovementSnapshotOccurrence).where(
                MovementSnapshotOccurrence.snapshot_id == second_snapshot.id
            )
        )
        assert first_link is not None and first_link.comparison_result == "FIRST_OBSERVED"
        assert second_link is not None
        assert second_link.comparison_result == "ALTERATION_OBSERVED"
        assert second_link.comparison_detail is not None
        assert second_link.comparison_detail["ambiguous"] is False
        assert len(second_link.comparison_detail["previous"]) == 1
        assert len(second_link.comparison_detail["current"]) == 1
        assert count_rows(session, MovementOccurrence) == 2


def test_movement_timestamp_preserves_original_and_normalizes_explicit_offset(
    migrated_engine: Engine,
) -> None:
    original = "2026-10-08T12:00:00-03:00"
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        ingest_page(
            session,
            collection_id=collection_id,
            page_key="aware-date",
            source="synthetic",
            page=make_page(make_hit(movements=[make_movement(85, "Citação", date_value=original)])),
        )

        occurrence = session.scalar(select(MovementOccurrence))
        assert occurrence is not None
        assert occurrence.source_date_original == original
        assert occurrence.source_date_status == "timezone_aware"
        assert occurrence.source_date_normalized == datetime(2026, 10, 8, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("include_movements", "movements", "complete", "rejection_count", "occurrence_count"),
    [
        (False, None, False, 1, 0),
        (True, None, False, 1, 0),
        (True, "invalid", False, 1, 0),
        (True, [], True, 0, 0),
        (True, [make_movement(85, "Válido"), 7], False, 1, 1),
    ],
)
def test_incomplete_movement_arrays_preserve_cover_and_local_rejections(
    migrated_engine: Engine,
    include_movements: bool,
    movements: JSONValue,
    complete: bool,
    rejection_count: int,
    occurrence_count: int,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        repeated_hit = make_hit(movements=movements, include_movements=include_movements)
        page = make_page(repeated_hit)
        result = ingest_page(
            session,
            collection_id=collection_id,
            page_key="movement-array",
            source="synthetic",
            page=page,
        )
        row_counts = (
            count_rows(session, MovementSnapshot),
            count_rows(session, MovementOccurrence),
            count_rows(session, QuarantineRejection),
        )
        replay = ingest_page(
            session,
            collection_id=collection_id,
            page_key="movement-array",
            source="synthetic",
            page=page,
        )

        snapshot = session.scalar(select(MovementSnapshot))
        assert snapshot is not None
        assert snapshot.is_complete is complete
        assert snapshot.rejection_count == rejection_count
        assert count_rows(session, RepresentationVersion) == 1
        assert count_rows(session, CollectionObservation) == 1
        assert count_rows(session, CollectionResult) == 1
        assert count_rows(session, MovementOccurrence) == occurrence_count
        assert len(result.quarantine_ids) == rejection_count
        assert replay.quarantine_ids == result.quarantine_ids
        assert row_counts == (
            count_rows(session, MovementSnapshot),
            count_rows(session, MovementOccurrence),
            count_rows(session, QuarantineRejection),
        )
        assert bool(result.incomplete_representation_ids) is not complete
        if occurrence_count:
            rejection = session.get(QuarantineRejection, result.quarantine_ids[0])
            assert rejection is not None
            assert rejection.error_path == "movimentos[1]"
            assert rejection.validation_code == "MOVEMENT_NOT_OBJECT"
            assert rejection.normalizer_version == NORMALIZER_VERSION
            assert rejection.raw_hit["source"] == session.scalar(
                select(RepresentationVersion.raw_payload)
            )


def test_absence_is_recorded_only_for_a_complete_snapshot_and_does_not_delete_history(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        first = ingest_page(
            session,
            collection_id=collection_id,
            page_key="present",
            source="synthetic",
            page=make_page(make_hit(movements=[make_movement(85, "Citação")])),
        )
        second = ingest_page(
            session,
            collection_id=collection_id,
            page_key="incomplete",
            source="synthetic",
            page=make_page(make_hit(movements=[7], sort_value=2)),
        )

        occurrence = session.scalar(select(MovementOccurrence))
        assert occurrence is not None
        assert occurrence.id
        assert count_rows(session, MovementOccurrence) == 1
        incomplete_snapshot = session.scalar(
            select(MovementSnapshot).where(MovementSnapshot.is_complete.is_(False))
        )
        assert incomplete_snapshot is not None
        incomplete_links = session.scalars(
            select(MovementSnapshotOccurrence).where(
                MovementSnapshotOccurrence.snapshot_id == incomplete_snapshot.id
            )
        ).all()
        assert incomplete_links == []

        third = ingest_page(
            session,
            collection_id=collection_id,
            page_key="complete-empty",
            source="synthetic",
            page=make_page(make_hit(movements=[], sort_value=3)),
        )
        empty_snapshot = session.scalar(
            select(MovementSnapshot).where(
                MovementSnapshot.version_id == third.versions[0].version_id
            )
        )
        assert empty_snapshot is not None and empty_snapshot.is_complete
        absence = session.scalars(
            select(MovementSnapshotOccurrence).where(
                MovementSnapshotOccurrence.snapshot_id == empty_snapshot.id
            )
        ).all()
        assert len(absence) == 1
        assert absence[0].present is False
        assert absence[0].comparison_result == "NOT_PRESENT_IN_SNAPSHOT"
        assert count_rows(session, MovementOccurrence) == 1
        assert first.versions[0].representation_id == second.representation_ids[0]


def test_invalid_cover_is_quarantined_without_creating_a_representation_and_replay_is_idempotent(
    migrated_engine: Engine,
) -> None:
    invalid_hit = make_hit(case_number="not-a-cnj")
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        first = ingest_page(
            session,
            collection_id=collection_id,
            page_key="invalid-cover",
            source="synthetic",
            page=make_page(invalid_hit),
        )
        second = ingest_page(
            session,
            collection_id=collection_id,
            page_key="invalid-cover",
            source="synthetic",
            page=make_page(invalid_hit, responded_at=OBSERVED_AT + timedelta(hours=1)),
        )

        assert first.representation_ids == ()
        assert first.observation_ids == ()
        assert first.quarantine_ids == second.quarantine_ids
        assert count_rows(session, QuarantineRejection) == 1
        rejection = session.get(QuarantineRejection, first.quarantine_ids[0])
        assert rejection is not None
        assert rejection.error_path == "_source.numeroProcesso"
        assert rejection.validation_code == "CNJ_INVALID"
        assert rejection.raw_hit["source"] == invalid_hit.source
        assert count_rows(session, Process) == 0
        assert count_rows(session, Representation) == 0


def test_quarantine_write_failure_rolls_back_all_page_effects(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_quarantine(*_args: object, **_kwargs: object) -> QuarantineRejection:
        raise RuntimeError("injected quarantine write failure")

    monkeypatch.setattr(QuarantineRepository, "create_or_get", fail_quarantine)
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        with pytest.raises(RuntimeError, match="injected quarantine write failure"):
            ingest_page(
                session,
                collection_id=collection_id,
                page_key="atomic-page",
                source="synthetic",
                page=make_page(make_hit(movements=[7])),
            )

        assert count_rows(session, Process) == 0
        assert count_rows(session, Representation) == 0
        assert count_rows(session, RepresentationVersion) == 0
        assert count_rows(session, CollectionObservation) == 0
        assert count_rows(session, MovementOccurrence) == 0
        assert count_rows(session, MovementSnapshot) == 0
        assert count_rows(session, QuarantineRejection) == 0
        assert count_rows(session, CollectionResult) == 0


def test_reprocessing_uses_a_corrected_normalizer_and_keeps_original_history(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agrojud.domain import movement_normalization

    corrected_version = "movement-normalizer-v2-test"

    def corrected(value: JSONValue):
        if isinstance(value, Mapping):
            normalized = normalize_movement(cast(Mapping[str, JSONValue], value))
        else:
            normalized = normalize_movement({"valorOriginal": value})
        return replace(normalized, algorithm_version=corrected_version)

    monkeypatch.setitem(
        movement_normalization.MOVEMENT_NORMALIZERS,
        corrected_version,
        cast(MovementNormalizer, corrected),
    )
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        ingested = ingest_page(
            session,
            collection_id=collection_id,
            page_key="reprocess",
            source="synthetic",
            page=make_page(make_hit(movements=[make_movement(85, "Citação"), "legacy"])),
        )
        rejection_id = ingested.quarantine_ids[0]
        original_observation_count = count_rows(session, CollectionObservation)
        original_result_count = count_rows(session, CollectionResult)
        original_version_id = ingested.versions[0].version_id
        original_representation = session.get(Representation, ingested.representation_ids[0])
        assert original_representation is not None
        original_last_observed_at = original_representation.last_observed_at
        original_subject_ids = tuple(
            sorted(
                session.scalars(
                    select(RepresentationSubject.id).where(
                        RepresentationSubject.representation_id == original_representation.id
                    )
                ).all()
            )
        )

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    results = reprocess_quarantine_ids(
        factory,
        [rejection_id],
        normalizer_version=corrected_version,
        resolved_at=OBSERVED_AT + timedelta(days=1),
    )
    assert results[0].status == "resolved"
    assert results[0].created_rejection_ids == ()

    with Session(migrated_engine) as session:
        rejection = session.get(QuarantineRejection, rejection_id)
        resolution = session.scalar(select(QuarantineResolution))
        corrected_snapshot = session.scalar(
            select(MovementSnapshot).where(
                MovementSnapshot.version_id == original_version_id,
                MovementSnapshot.normalizer_version == corrected_version,
            )
        )
        assert rejection is not None and rejection.status == "resolved"
        assert resolution is not None and resolution.rejection_id == rejection_id
        assert resolution.normalizer_version == corrected_version
        assert corrected_snapshot is not None and corrected_snapshot.is_complete
        assert corrected_snapshot.processed_at == OBSERVED_AT + timedelta(days=1)
        assert count_rows(session, CollectionObservation) == original_observation_count
        assert count_rows(session, CollectionResult) == original_result_count
        assert count_rows(session, QuarantineRejection) == 1
        assert count_rows(session, QuarantineResolution) == 1
        assert count_rows(session, MovementOccurrence) == 3
        corrected_occurrences = session.scalars(
            select(MovementOccurrence).where(
                MovementOccurrence.normalizer_version == corrected_version
            )
        ).all()
        assert len(corrected_occurrences) == 2
        assert {item.first_observed_at for item in corrected_occurrences} == {OBSERVED_AT}
        representation = session.get(Representation, results[0].representation_id)
        assert representation is not None
        assert representation.last_observed_at == original_last_observed_at
        subject_ids = tuple(
            sorted(
                session.scalars(
                    select(RepresentationSubject.id).where(
                        RepresentationSubject.representation_id == representation.id
                    )
                ).all()
            )
        )
        assert subject_ids == original_subject_ids

    replay = reprocess_quarantine_ids(
        factory,
        [rejection_id],
        normalizer_version=corrected_version,
        resolved_at=OBSERVED_AT + timedelta(days=2),
    )
    assert replay[0].status == "already_resolved"
    with Session(migrated_engine) as session:
        assert count_rows(session, QuarantineResolution) == 1
        assert count_rows(session, MovementOccurrence) == 3


def test_empty_snapshot_retains_the_selected_normalizer_version(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agrojud.domain import movement_normalization

    normalizer_version = "movement-normalizer-empty-test"

    def versioned_normalizer(value: JSONValue):
        return replace(
            normalize_movement(cast(Mapping[str, JSONValue], value)),
            algorithm_version=normalizer_version,
        )

    monkeypatch.setitem(
        movement_normalization.MOVEMENT_NORMALIZERS,
        normalizer_version,
        cast(MovementNormalizer, versioned_normalizer),
    )
    with Session(migrated_engine) as session, session.begin():
        collection_id = new_collection(session)
        ingested = ingest_page(
            session,
            collection_id=collection_id,
            page_key="empty-normalizer-version",
            source="synthetic",
            page=make_page(make_hit(movements=[])),
        )
        version = session.get(RepresentationVersion, ingested.versions[0].version_id)
        assert version is not None
        assert isinstance(version.raw_payload, dict)

        processed = persist_movement_snapshot(
            session,
            representation_id=ingested.versions[0].representation_id,
            version_id=version.id,
            raw_source=cast(Mapping[str, JSONValue], version.raw_payload),
            observed_at=OBSERVED_AT + timedelta(days=1),
            first_observed_at=OBSERVED_AT,
            normalizer_version=normalizer_version,
        )

        assert processed.snapshot.normalizer_version == normalizer_version
        assert processed.snapshot.is_complete
        assert processed.snapshot.rejection_count == 0


def test_reprocessing_requires_existing_explicit_ids(migrated_engine: Engine) -> None:
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    with pytest.raises(LookupError, match="não existe; nenhum item foi varrido"):
        reprocess_quarantine_ids(factory, [uuid4()])
