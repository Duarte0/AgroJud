"""PostgreSQL acceptance tests for SPEC-016 historical baselines and news."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config, upgrade_database
from agrojud.db.models import (
    MovementOccurrence,
    MovementSnapshot,
    Process,
    ProcessNews,
    ProcessWatchCycle,
    ProcessWatchlistEntry,
    QuarantineRejection,
    Representation,
    RepresentationVersion,
    RepresentationWatchBaseline,
)
from agrojud.domain.canonical_json import JSONValue
from agrojud.domain.movement_normalization import MovementNormalizer
from agrojud.domain.occurrence_identity import normalize_movement
from agrojud.services.collection import build_collection_request
from agrojud.services.ingestion import create_collection, ingest_page
from agrojud.services.jobs import LeaseLostError
from agrojud.services.movement_ingestion import persist_movement_snapshot
from agrojud.services.quarantine_reprocessing import reprocess_quarantine_ids
from agrojud.sources.contracts import SourceHit, SourcePage, build_query_by_case_number

CNJ = "00000010020268090001"


def current_time() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def news_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    app = create_app(
        Settings(
            environment="demo",
            database_url=scratch_database_url.render_as_string(hide_password=False),
        ),
        engine,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


def movement(
    code: int,
    name: str,
    *,
    event_date: str = "2018-01-02T03:04:05Z",
) -> dict[str, JSONValue]:
    return {
        "codigo": code,
        "dataHora": event_date,
        "nome": name,
        "orgaoJulgador": {"codigo": 123, "nome": "Vara sintética"},
        "complementosTabelados": [{"codigo": 1, "valor": "fixture"}],
    }


def hit(
    *,
    source_id: str = "origin-a",
    movements: JSONValue = None,
    case_number: str = CNJ,
) -> SourceHit:
    source: dict[str, JSONValue] = {
        "id": source_id,
        "numeroProcesso": case_number,
        "tribunal": "TJGO",
        "dataAjuizamento": "20260110120000",
        "@timestamp": "2026-10-09T10:00:00Z",
        "grau": "G1",
        "classe": {"codigo": 1116, "nome": "Execução sintética"},
        "assuntos": [{"codigo": 4968, "nome": "Crédito rural sintético"}],
        "orgaoJulgador": {"codigo": 123, "nome": "Vara sintética"},
        "movimentos": movements,
    }
    return SourceHit(
        source_id=source_id,
        source=source,
        sort_values=(1,),
        raw={"_id": source_id, "_source": source, "sort": [1]},
    )


def page(
    selected_hit: SourceHit,
    *,
    observed_at: datetime,
) -> SourcePage:
    return SourcePage(
        hits=(selected_hit,),
        total_value=1,
        total_relation="eq",
        cursor_final=(1,),
        responded_at=observed_at,
    )


def collect(
    app: FastAPI,
    selected_hit: SourceHit,
    *,
    page_key: str,
    observed_at: datetime,
    collection_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    with Session(app.state.database_engine) as session, session.begin():
        if collection_id is None:
            collection_id = create_collection(
                session,
                mode="demo",
                resolved_criteria={"manual_test": "SPEC-016"},
            ).id
        ingest_page(
            session,
            collection_id=collection_id,
            page_key=page_key,
            source="synthetic",
            page=page(selected_hit, observed_at=observed_at),
        )
        process_id = session.scalar(select(Process.id).where(Process.numero_cnj == CNJ))
        if process_id is None:
            raise AssertionError("A ingestão não persistiu o processo esperado.")
        return collection_id, process_id


def news_count(app: FastAPI) -> int:
    with Session(app.state.database_engine) as session:
        return cast(int, session.scalar(select(func.count()).select_from(ProcessNews)) or 0)


def include(client: TestClient, process_id: UUID) -> dict[str, object]:
    response = client.put(f"/api/v1/processes/{process_id}/watch")
    assert response.status_code == 200
    return response.json()


def test_partial_version_stays_pending_until_first_complete_snapshot_is_baseline(
    news_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = news_runtime
    partial = hit(movements=[movement(1, "Histórico conhecido"), "rejeitado"])
    _, process_id = collect(app, partial, page_key="initial", observed_at=current_time())

    watch = include(client, process_id)
    assert watch["baselines"][0]["state"] == "pending"
    assert news_count(app) == 0

    full = hit(movements=[movement(1, "Histórico conhecido"), movement(2, "Histórico recuperado")])
    _, _ = collect(
        app,
        full,
        page_key="first-complete",
        observed_at=current_time(),
    )
    baseline = client.get(f"/api/v1/processes/{process_id}/watch").json()["baselines"][0]
    assert baseline["state"] == "established"
    assert baseline["version_id"]
    assert news_count(app) == 0

    after_baseline = hit(
        movements=[
            movement(1, "Histórico conhecido"),
            movement(2, "Histórico recuperado"),
            movement(3, "Observação posterior"),
        ]
    )
    first_observed_at = current_time()
    collect(
        app,
        after_baseline,
        page_key="after-baseline",
        observed_at=first_observed_at,
    )
    result = client.get(
        "/api/v1/news",
        params={"process_number": "0000001-00.2026.8.09.0001", "status": "pending"},
    )
    assert result.status_code == 200
    assert result.json()["total"] == 1
    item = result.json()["items"][0]
    assert item["category"] == "NEW_OBSERVATION"
    assert item["event_date"] == "2018-01-02T03:04:05Z"
    assert datetime.fromisoformat(item["first_observed_at"].replace("Z", "+00:00")) == (
        first_observed_at
    )


def test_new_representation_emits_one_event_and_its_incomplete_history_is_baselined(
    news_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = news_runtime
    _, process_id = collect(
        app,
        hit(movements=[movement(1, "Baseline")]),
        page_key="rep-a",
        observed_at=current_time(),
    )
    include(client, process_id)

    _, same_process_id = collect(
        app,
        hit(
            source_id="origin-b",
            movements=[movement(10, "Capa nova"), "inválido"],
        ),
        page_key="rep-b-partial",
        observed_at=current_time(),
    )
    assert same_process_id == process_id
    response = client.get("/api/v1/news", params={"process_id": str(process_id)})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["category"] == "NEW_REPRESENTATION"

    complete = hit(
        source_id="origin-b",
        movements=[movement(10, "Capa nova"), movement(11, "Histórico da capa")],
    )
    collect(
        app,
        complete,
        page_key="rep-b-complete",
        observed_at=current_time(),
    )
    assert news_count(app) == 1
    watch = client.get(f"/api/v1/processes/{process_id}/watch").json()
    assert len(watch["baselines"]) == 2
    assert all(item["state"] == "established" for item in watch["baselines"])


def test_alterations_multiplicity_reordering_and_review_survive_replay_and_reinclude(
    news_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = news_runtime
    _, process_id = collect(
        app,
        hit(movements=[movement(1, "Texto original"), movement(2, "Outro movimento")]),
        page_key="baseline",
        observed_at=current_time(),
    )
    include(client, process_id)

    changed = hit(movements=[movement(2, "Outro movimento"), movement(1, "Texto corrigido")])
    changed_collection, _ = collect(
        app,
        changed,
        page_key="alteration",
        observed_at=current_time(),
    )
    items = client.get("/api/v1/news").json()["items"]
    assert [item["category"] for item in items] == ["ALTERATION_OBSERVED"]
    item_id = items[0]["id"]
    assert items[0]["evidence"]["alteration"]["previous_occurrences"]

    reviewed = client.patch(f"/api/v1/news/{item_id}", json={"status": "reviewed"})
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
    exact_replay = client.get("/api/v1/news", params={"status": "reviewed"})
    assert exact_replay.json()["total"] == 1

    # Replays of the same exact page do not add another copy or erase review.
    collect(
        app,
        changed,
        page_key="alteration",
        observed_at=current_time(),
        collection_id=changed_collection,
    )
    reviewed_after_replay = client.get("/api/v1/news", params={"status": "reviewed"})
    assert reviewed_after_replay.json()["total"] == 1
    assert reviewed_after_replay.json()["items"][0]["status"] == "reviewed"

    client.delete(f"/api/v1/processes/{process_id}/watch")
    reintroduced = include(client, process_id)
    assert reintroduced["baselines"]
    assert news_count(app) == 1
    reviewed_after_reinclude = client.get("/api/v1/news", params={"status": "reviewed"})
    assert reviewed_after_reinclude.json()["total"] == 1


def test_duplicate_multiplicity_and_reordering_do_not_duplicate_news(
    news_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = news_runtime
    one = movement(1, "Duplicado")
    other = movement(2, "Outro")
    _, process_id = collect(
        app,
        hit(movements=[one, other]),
        page_key="base",
        observed_at=current_time(),
    )
    include(client, process_id)

    for index, movements in enumerate(([other, one, one], [one], [one, other, one]), start=1):
        collect(
            app,
            hit(movements=list(movements)),
            page_key=f"multiplicity-{index}",
            observed_at=current_time(),
        )
        assert news_count(app) == 1
    result = client.get("/api/v1/news")
    assert result.json()["items"][0]["category"] == "NEW_OBSERVATION"
    assert result.json()["items"][0]["evidence"]["identity"]["multiplicity_ordinal"] == 2


def test_removal_disables_news_and_reinclusion_does_not_create_gap_backlog(
    news_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = news_runtime
    _, process_id = collect(
        app,
        hit(movements=[movement(1, "Antes de acompanhar")]),
        page_key="base",
        observed_at=current_time(),
    )
    include(client, process_id)
    client.delete(f"/api/v1/processes/{process_id}/watch")

    during_gap = hit(movements=[movement(1, "Antes de acompanhar"), movement(2, "No intervalo")])
    collection_id, _ = collect(
        app,
        during_gap,
        page_key="gap",
        observed_at=current_time(),
    )
    assert news_count(app) == 0

    include(client, process_id)
    collect(
        app,
        during_gap,
        page_key="after-reinclude",
        observed_at=current_time(),
        collection_id=collection_id,
    )
    assert news_count(app) == 0

    after_reinclude = hit(
        movements=[
            movement(1, "Antes de acompanhar"),
            movement(2, "No intervalo"),
            movement(3, "Posterior"),
        ]
    )
    collect(
        app,
        after_reinclude,
        page_key="new-cycle",
        observed_at=current_time(),
    )
    assert news_count(app) == 1
    with Session(app.state.database_engine) as session:
        cycles = session.scalars(
            select(ProcessWatchCycle)
            .where(ProcessWatchCycle.process_id == process_id)
            .order_by(ProcessWatchCycle.cycle_number)
        ).all()
        assert [cycle.cycle_number for cycle in cycles] == [1, 2]
        assert cycles[0].ended_at is not None and cycles[1].ended_at is None


def test_news_page_rollback_and_lost_lease_leave_no_orphan_rows(
    news_runtime: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = news_runtime
    _, process_id = collect(
        app,
        hit(movements=[movement(1, "Baseline")]),
        page_key="base",
        observed_at=current_time(),
    )
    include(client, process_id)

    from agrojud.services import news as news_service

    original_insert = news_service._insert_news

    def fail_after_insert(*args: object, **kwargs: object) -> None:
        original_insert(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("simulated outer page rollback")

    monkeypatch.setattr(news_service, "_insert_news", fail_after_insert)
    rollback_collection = create_collection_for_test(app)
    with pytest.raises(RuntimeError, match="simulated outer page rollback"):
        with Session(app.state.database_engine) as session, session.begin():
            ingest_page(
                session,
                collection_id=rollback_collection,
                page_key="rollback",
                source="synthetic",
                page=page(
                    hit(movements=[movement(1, "Baseline"), movement(2, "Rollback")]),
                    observed_at=current_time(),
                ),
            )
    monkeypatch.setattr(news_service, "_insert_news", original_insert)
    assert news_count(app) == 0

    jobs = app.state.jobs
    created = jobs.enqueue(
        build_collection_request(
            mode="demo",
            source="synthetic",
            job_type="refresh_number",
            query=build_query_by_case_number(CNJ),
        )
    )
    old_lease = jobs.claim("news-old-worker")
    assert old_lease is not None
    with app.state.database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE jobs SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :job_id"
            ),
            {"job_id": created.job_id},
        )
    replacement = jobs.claim("news-new-worker")
    assert replacement is not None
    assert replacement.possession_token != old_lease.possession_token

    with pytest.raises(LeaseLostError):
        jobs.commit_page(
            old_lease.job_id,
            old_lease.possession_token,
            expected_revision=0,
            budget_limit=2_000,
            source="synthetic",
            page=page(
                hit(movements=[movement(1, "Baseline"), movement(3, "Lost lease")]),
                observed_at=current_time(),
            ),
        )
    assert news_count(app) == 0
    with Session(app.state.database_engine) as session:
        baseline = session.scalar(
            select(RepresentationWatchBaseline).where(
                RepresentationWatchBaseline.process_id == process_id
            )
        )
        assert baseline is not None and baseline.state == "established"
        assert session.scalar(select(func.count()).select_from(MovementOccurrence)) == 1


def create_collection_for_test(app: FastAPI) -> UUID:
    with Session(app.state.database_engine) as session, session.begin():
        return create_collection(
            session,
            mode="demo",
            resolved_criteria={"manual_test": "rollback"},
        ).id


def test_quarantine_reprocessing_can_establish_baseline_and_publish_local_alteration(
    news_runtime: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agrojud.domain import movement_normalization

    app, client = news_runtime
    corrected_version = "movement-normalizer-v2-news-test"

    def corrected(value: JSONValue):
        if isinstance(value, dict):
            normalized = normalize_movement(cast(dict[str, JSONValue], value))
        else:
            normalized = normalize_movement(movement(1, "Texto original"))
        return replace(normalized, algorithm_version=corrected_version)

    monkeypatch.setitem(
        movement_normalization.MOVEMENT_NORMALIZERS,
        corrected_version,
        cast(MovementNormalizer, corrected),
    )

    _, process_id = collect(
        app,
        hit(movements=[movement(1, "Texto original")]),
        page_key="initial",
        observed_at=current_time(),
    )
    with Session(app.state.database_engine) as session, session.begin():
        representation = session.scalar(
            select(Representation).where(Representation.process_id == process_id)
        )
        assert representation is not None and representation.latest_version_id is not None
        version_id = representation.latest_version_id
        stored_version = session.get(RepresentationVersion, version_id)
        assert stored_version is not None
        persist_movement_snapshot(
            session,
            representation_id=representation.id,
            version_id=stored_version.id,
            raw_source=cast(dict[str, JSONValue], stored_version.raw_payload),
            observed_at=current_time(),
            normalizer_version=corrected_version,
        )

    include(client, process_id)
    baseline = client.get(f"/api/v1/processes/{process_id}/watch").json()["baselines"][0]
    assert baseline["state"] == "established"

    changed = hit(movements=[movement(1, "Texto corrigido"), "legado"])
    _, _ = collect(
        app,
        changed,
        page_key="changed-partial",
        observed_at=current_time(),
    )
    assert news_count(app) == 0
    with Session(app.state.database_engine) as session:
        rejection_id = session.scalar(select(QuarantineRejection.id))
    assert rejection_id is not None

    reprocess_quarantine_ids(
        sessionmaker(app.state.database_engine, expire_on_commit=False),
        [rejection_id],
        normalizer_version=corrected_version,
        resolved_at=current_time(),
    )
    response = client.get("/api/v1/news")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["category"] == "ALTERATION_OBSERVED"
    assert item["provenance"] == "quarantine_reprocess"
    reviewed = client.patch(f"/api/v1/news/{item['id']}", json={"status": "reviewed"})
    assert reviewed.status_code == 200
    replay = reprocess_quarantine_ids(
        sessionmaker(app.state.database_engine, expire_on_commit=False),
        [rejection_id],
        normalizer_version=corrected_version,
        resolved_at=current_time(),
    )
    assert replay[0].status == "already_resolved"
    assert news_count(app) == 1
    assert client.get("/api/v1/news", params={"status": "reviewed"}).json()["total"] == 1


def test_migration_backfills_active_watch_baselines_without_losing_process(
    scratch_database_url: URL,
) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            command.upgrade(make_alembic_config(connection), "20261009_0008")
        with Session(engine) as session, session.begin():
            process = Process(numero_cnj=CNJ)
            session.add(process)
            session.flush()
            pending_representation = Representation(
                process_id=process.id,
                source="synthetic",
                tribunal="TJGO",
                source_id="pre-existing-pending",
            )
            complete_representation = Representation(
                process_id=process.id,
                source="synthetic",
                tribunal="TJGO",
                source_id="pre-existing-complete",
            )
            session.add_all([pending_representation, complete_representation])
            session.flush()
            observed_at = datetime(2026, 10, 2, tzinfo=UTC)
            version = RepresentationVersion(
                representation_id=complete_representation.id,
                payload_sha256="a" * 64,
                raw_payload={},
                normalizer_version="movement-normalizer-v1",
                first_observed_at=observed_at,
            )
            session.add(version)
            session.flush()
            complete_representation.latest_version_id = version.id
            session.add(
                MovementSnapshot(
                    representation_id=complete_representation.id,
                    version_id=version.id,
                    normalizer_version="movement-normalizer-v1",
                    is_complete=True,
                    rejection_count=0,
                    result_sha256="b" * 64,
                    processed_at=observed_at,
                )
            )
            session.add(
                ProcessWatchlistEntry(
                    process_id=process.id,
                    active=True,
                    included_at=datetime(2026, 10, 1, tzinfo=UTC),
                )
            )
            process_id = process.id
            complete_representation_id = complete_representation.id

        with engine.begin() as connection:
            upgrade_database(connection)
            assert MigrationContext.configure(connection).get_current_revision() == "20261009_0009"

        with Session(engine) as session:
            assert session.get(Process, process_id) is not None
            cycle = session.scalar(
                select(ProcessWatchCycle).where(ProcessWatchCycle.process_id == process_id)
            )
            baselines = session.scalars(
                select(RepresentationWatchBaseline).where(
                    RepresentationWatchBaseline.process_id == process_id
                )
            ).all()
            assert cycle is not None and cycle.cycle_number == 1 and cycle.ended_at is None
            assert len(baselines) == 2
            by_representation = {baseline.representation_id: baseline for baseline in baselines}
            pending_baseline = next(
                baseline for baseline in baselines if baseline.state == "pending"
            )
            complete_baseline = by_representation[complete_representation_id]
            assert pending_baseline.version_id is None and pending_baseline.established_at is None
            assert complete_baseline.state == "established"
            assert complete_baseline.version_id is not None
            assert complete_baseline.established_at == observed_at
    finally:
        engine.dispose()
