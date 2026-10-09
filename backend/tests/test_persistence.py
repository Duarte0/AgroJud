"""PostgreSQL integration coverage for SPEC-005 source-cover persistence."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from alembic import command
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config, upgrade_database
from agrojud.db.models import (
    Base,
    Collection,
    CollectionObservation,
    CollectionResult,
    Process,
    Representation,
    RepresentationSubject,
    RepresentationVersion,
)
from agrojud.domain.canonical_json import JSONValue
from agrojud.domain.persistence import (
    ObservationPositionConflict,
    PayloadHashCollisionError,
    SourceIdentityConflict,
)
from agrojud.services.ingestion import create_collection, ingest_page
from agrojud.sources.contracts import SourceHit, SourcePage

CASE_A = "00000010020248090001"
CASE_B = "00000020020248090002"
SOURCE_A = "fixture-origin-a"
SOURCE_B = "fixture-origin-b"
OBSERVED_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
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
    "process_signals",
    "process_triage",
    "process_triage_history",
    "process_watchlist_entries",
    "process_watchlist_history",
    "process_watch_cycles",
    "quarantine_rejections",
    "quarantine_resolutions",
    "process_news",
    "queue_claim_state",
    "representation_subjects",
    "representation_watch_baselines",
    "representation_versions",
    "representations",
    "signal_evaluations",
    "signal_run_inputs",
    "signal_run_processes",
    "source_rate_limits",
    "saved_searches",
    "saved_search_versions",
    "schedule_dispatches",
}


@pytest.fixture
def migrated_engine(scratch_database_url: URL) -> Iterator[Engine]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    yield engine
    engine.dispose()


def make_hit(
    *,
    case_number: str = CASE_A,
    source_id: str = SOURCE_A,
    filed_at: JSONValue = "20260110120000",
    source_updated_at: JSONValue = "2026-01-11T12:00:00Z",
    class_code: JSONValue = 1116,
    class_name: JSONValue = "Execução de título extrajudicial sintética",
    degree: JSONValue = "G1",
    court_code: JSONValue = 123,
    court_name: JSONValue = "Vara sintética",
    subjects: JSONValue | None = None,
    extra: JSONValue = "fixture",
) -> SourceHit:
    if subjects is None:
        subjects = [{"codigo": 4968, "nome": "Cédula de crédito rural sintética"}]
    source = {
        "id": source_id,
        "numeroProcesso": case_number,
        "tribunal": "TJGO",
        "dataAjuizamento": filed_at,
        "@timestamp": source_updated_at,
        "grau": degree,
        "classe": {"codigo": class_code, "nome": class_name},
        "assuntos": subjects,
        "orgaoJulgador": {"codigo": court_code, "nome": court_name},
        "movimentos": [],
        "campoSintetico": extra,
    }
    return SourceHit(
        source_id=source_id,
        source=source,
        sort_values=(1,),
        raw={"_id": source_id, "_source": source, "sort": [1]},
    )


def make_page(*hits: SourceHit, responded_at: datetime = OBSERVED_AT) -> SourcePage:
    return SourcePage(
        hits=hits,
        total_value=len(hits),
        total_relation="eq",
        cursor_final=hits[-1].sort_values if hits else None,
        responded_at=responded_at,
    )


def new_collection(session: Session, *, criteria: dict[str, JSONValue] | None = None) -> Collection:
    return create_collection(
        session,
        mode="demo",
        resolved_criteria=criteria or {"preset": "fixture-v1"},
    )


def count_rows(session: Session, model: type[Base]) -> int:
    return cast(int, session.scalar(select(func.count()).select_from(model)) or 0)


def test_migration_applies_from_empty_and_foundation_revision(scratch_database_url: URL) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            assert inspect(connection).get_table_names() == []
            command.upgrade(make_alembic_config(connection), "20261008_0001")
            assert inspect(connection).get_table_names() == ["alembic_version"]
            upgrade_database(connection)
            assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_migration_applies_to_a_fresh_empty_database(scratch_database_url: URL) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            assert inspect(connection).get_table_names() == []
            upgrade_database(connection)
            assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_collection_criteria_hash_uses_canonical_json(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        first = new_collection(session, criteria={"z": [1, None], "a": True})
        second = new_collection(session, criteria={"a": True, "z": [1, None]})
        assert first.id != second.id
        assert first.criteria_sha256 == second.criteria_sha256


def test_replaying_the_same_page_preserves_counts_associations_and_response(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection = new_collection(session)
        page = make_page(make_hit())

        first = ingest_page(
            session,
            collection_id=collection.id,
            page_key="initial",
            source="synthetic",
            page=page,
        )
        before = tuple(
            count_rows(session, model)
            for model in (
                Process,
                Representation,
                RepresentationVersion,
                CollectionObservation,
                CollectionResult,
                RepresentationSubject,
            )
        )
        replay = ingest_page(
            session,
            collection_id=collection.id,
            page_key="initial",
            source="synthetic",
            page=make_page(make_hit(), responded_at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC)),
        )
        after = tuple(
            count_rows(session, model)
            for model in (
                Process,
                Representation,
                RepresentationVersion,
                CollectionObservation,
                CollectionResult,
                RepresentationSubject,
            )
        )

        assert first.counts == replay.counts
        assert first.counts.new == 1
        assert first.representation_ids == replay.representation_ids
        assert first.observation_ids == replay.observation_ids
        assert first.versions == replay.versions
        assert before == after == (1, 1, 1, 1, 1, 1)
        representation = session.get(Representation, first.representation_ids[0])
        observation = session.get(CollectionObservation, first.observation_ids[0])
        assert representation is not None
        assert observation is not None
        assert representation.last_observed_at == OBSERVED_AT
        assert observation.observed_at == OBSERVED_AT


def test_new_collection_reuses_version_but_adds_observation_and_result(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        first_collection = new_collection(session)
        first = ingest_page(
            session,
            collection_id=first_collection.id,
            page_key="initial",
            source="synthetic",
            page=make_page(make_hit()),
        )
        second_collection = new_collection(session)
        second = ingest_page(
            session,
            collection_id=second_collection.id,
            page_key="initial",
            source="synthetic",
            page=make_page(make_hit(), responded_at=datetime(2026, 10, 10, tzinfo=UTC)),
        )

        assert first.counts.new == 1
        assert second.counts.unchanged == 1
        assert first.versions[0].version_id == second.versions[0].version_id
        assert count_rows(session, RepresentationVersion) == 1
        assert count_rows(session, CollectionObservation) == 2
        assert count_rows(session, CollectionResult) == 2


def test_distinct_source_covers_for_same_cnj_remain_separate(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection = new_collection(session)
        result = ingest_page(
            session,
            collection_id=collection.id,
            page_key="initial",
            source="synthetic",
            page=make_page(
                make_hit(source_id=SOURCE_A),
                make_hit(source_id=SOURCE_B, class_code=1208, degree="G2", court_code=456),
            ),
        )

        processes = session.scalars(select(Process)).all()
        representations = session.scalars(
            select(Representation).order_by(Representation.source_id)
        ).all()
        assert len(processes) == 1
        assert processes[0].numero_cnj == CASE_A
        assert len(representations) == 2
        assert {representation.process_id for representation in representations} == {
            processes[0].id
        }
        assert len({representation.id for representation in representations}) == 2
        assert result.counts.new == 2


def test_array_order_creates_a_raw_version_but_keeps_one_collection_result(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection = new_collection(session)
        first_subjects: list[JSONValue] = [
            {"codigo": 1, "nome": "Assunto sintético A"},
            {"codigo": 2, "nome": "Assunto sintético B"},
        ]
        reversed_subjects: JSONValue = list(reversed(first_subjects))
        result = ingest_page(
            session,
            collection_id=collection.id,
            page_key="array-order",
            source="synthetic",
            page=make_page(
                make_hit(subjects=first_subjects),
                make_hit(subjects=reversed_subjects),
            ),
        )

        assert result.counts.new == 1
        assert len(result.observation_ids) == 2
        assert len(result.versions) == 2
        assert result.versions[0].payload_sha256 != result.versions[1].payload_sha256
        assert count_rows(session, CollectionObservation) == 2
        assert count_rows(session, CollectionResult) == 1
        assert count_rows(session, RepresentationVersion) == 2
        with pytest.raises(ObservationPositionConflict):
            ingest_page(
                session,
                collection_id=collection.id,
                page_key="array-order",
                source="synthetic",
                page=make_page(make_hit(subjects=first_subjects)),
            )
        assert count_rows(session, CollectionObservation) == 2
        assert count_rows(session, CollectionResult) == 1
        subjects = session.scalars(
            select(RepresentationSubject).order_by(RepresentationSubject.ordinal)
        ).all()
        assert [subject.subject_code for subject in subjects] == ["2", "1"]


def test_external_rollback_removes_collection_and_every_page_effect(
    migrated_engine: Engine,
) -> None:
    engine = migrated_engine
    with Session(engine) as session:
        session.begin()
        collection = new_collection(session)
        ingest_page(
            session,
            collection_id=collection.id,
            page_key="initial",
            source="synthetic",
            page=make_page(make_hit()),
        )
        assert count_rows(session, Process) == 1
        assert count_rows(session, RepresentationVersion) == 1
        session.rollback()

    with Session(engine) as session:
        assert count_rows(session, Collection) == 0
        assert count_rows(session, Process) == 0
        assert count_rows(session, Representation) == 0
        assert count_rows(session, RepresentationVersion) == 0
        assert count_rows(session, CollectionObservation) == 0
        assert count_rows(session, CollectionResult) == 0
        assert count_rows(session, RepresentationSubject) == 0


def test_identity_conflict_rolls_back_prior_hits_from_the_page(migrated_engine: Engine) -> None:
    engine = migrated_engine
    with Session(engine) as session, session.begin():
        seed_collection = new_collection(session)
        ingest_page(
            session,
            collection_id=seed_collection.id,
            page_key="seed",
            source="synthetic",
            page=make_page(make_hit(source_id="stable-origin")),
        )

    with Session(engine) as session:
        session.begin()
        collection = new_collection(session)
        page = make_page(
            make_hit(case_number=CASE_B, source_id="new-origin"),
            make_hit(case_number="00000030020248090003", source_id="stable-origin"),
        )
        with pytest.raises(SourceIdentityConflict):
            ingest_page(
                session,
                collection_id=collection.id,
                page_key="conflicting",
                source="synthetic",
                page=page,
            )

        # The nested savepoint removed the new process, representation, and first hit.
        assert count_rows(session, Process) == 1
        assert count_rows(session, Representation) == 1
        assert count_rows(session, RepresentationVersion) == 1
        assert count_rows(session, CollectionObservation) == 1
        assert count_rows(session, CollectionResult) == 1
        session.rollback()


def test_conflicting_replay_and_hash_collision_are_integrity_errors(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection = new_collection(session)
        original = make_hit()
        ingest_page(
            session,
            collection_id=collection.id,
            page_key="fixed",
            source="synthetic",
            page=make_page(original),
        )
        with pytest.raises(ObservationPositionConflict):
            ingest_page(
                session,
                collection_id=collection.id,
                page_key="fixed",
                source="synthetic",
                page=make_page(make_hit(extra="different content")),
            )
        assert count_rows(session, CollectionObservation) == 1
        assert count_rows(session, CollectionResult) == 1

        monkeypatch.setattr("agrojud.services.ingestion.sha256_json", lambda _: "a" * 64)
        collision_collection = new_collection(session)
        ingest_page(
            session,
            collection_id=collision_collection.id,
            page_key="first-collision-content",
            source="synthetic",
            page=make_page(
                make_hit(
                    case_number=CASE_B,
                    source_id="collision-origin",
                    extra="first payload for forced hash",
                )
            ),
        )
        second_collision_collection = new_collection(session)
        with pytest.raises(PayloadHashCollisionError):
            ingest_page(
                session,
                collection_id=second_collision_collection.id,
                page_key="collision",
                source="synthetic",
                page=make_page(
                    make_hit(
                        case_number=CASE_B,
                        source_id="collision-origin",
                        extra="content with the forced same hash",
                    )
                ),
            )
        assert count_rows(session, RepresentationVersion) == 2
        assert count_rows(session, CollectionObservation) == 2


def test_structured_fields_timezone_and_source_filing_date_regression(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        first_collection = new_collection(session)
        first = ingest_page(
            session,
            collection_id=first_collection.id,
            page_key="first",
            source="synthetic",
            page=make_page(
                make_hit(filed_at="2026-01-10T12:00:00-03:00"),
                responded_at=datetime(2026, 1, 10, tzinfo=UTC),
            ),
        )
        representation = session.get(Representation, first.representation_ids[0])
        assert representation is not None
        assert representation.class_code == "1116"
        assert representation.grau == "G1"
        assert representation.court_unit_code == "123"
        assert representation.source_filed_at_original == "2026-01-10T12:00:00-03:00"
        assert representation.source_filed_at == datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
        assert len(session.scalars(select(RepresentationSubject)).all()) == 1

        second_collection = new_collection(session)
        second = ingest_page(
            session,
            collection_id=second_collection.id,
            page_key="older-source-date",
            source="synthetic",
            page=make_page(
                make_hit(
                    filed_at="2026-01-09T18:00:00Z",
                    extra="a later payload with an earlier filing date",
                ),
                responded_at=datetime(2026, 1, 11, tzinfo=UTC),
            ),
        )

        assert second.counts.updated == 1
        assert len(second.date_regressions) == 1
        regression = second.date_regressions[0]
        observation = session.get(CollectionObservation, second.observation_ids[0])
        representation = session.get(Representation, second.representation_ids[0])
        assert observation is not None
        assert representation is not None
        assert observation.source_filed_at_regressed is True
        assert observation.regressed_from_version_id == regression.previous_version_id
        assert representation.latest_version_id == regression.observed_version_id
        assert regression.previous_source_date_original == "2026-01-10T12:00:00-03:00"
        assert regression.observed_source_date_original == "2026-01-09T18:00:00Z"


def test_timezone_less_source_date_stays_original_and_has_no_inferred_instant(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        collection = new_collection(session)
        result = ingest_page(
            session,
            collection_id=collection.id,
            page_key="compact-date",
            source="synthetic",
            page=make_page(make_hit(filed_at="20261009123456")),
        )
        representation = session.get(Representation, result.representation_ids[0])
        version = session.get(RepresentationVersion, result.versions[0].version_id)
        assert representation is not None
        assert version is not None
        assert representation.source_filed_at_original == "20261009123456"
        assert representation.source_filed_at is None
        assert representation.source_filed_at_timezone_ambiguous is True
        assert version.source_filed_at_original == "20261009123456"
        assert version.source_filed_at is None
        assert version.source_filed_at_timezone_ambiguous is True
