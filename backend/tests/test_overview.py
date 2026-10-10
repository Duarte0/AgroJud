"""PostgreSQL acceptance coverage for SPEC-018 local-base indicators."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.api.overview import (
    _latest_collections_statement,
    _overview_statistics_statement,
    _selected_processes,
    _signal_distribution_statement,
    _theme_distribution_statement,
)
from agrojud.api.process_filters import ProcessFilters
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Collection,
    Job,
    Process,
    ProcessNews,
    ProcessSignal,
    ProcessTriage,
    ProcessWatchCycle,
    ProcessWatchlistEntry,
    Representation,
    RepresentationSubject,
)

OBSERVED_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def overview_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    database_url = scratch_database_url.render_as_string(hide_password=False)
    engine = make_engine(database_url)
    with engine.begin() as connection:
        upgrade_database(connection)
    app = create_app(Settings(environment="demo", database_url=database_url), engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


@pytest.fixture
def overview_seed(overview_runtime: tuple[FastAPI, TestClient]) -> dict[str, UUID]:
    app, _client = overview_runtime
    ids = {key: uuid4() for key in ("p1", "p2", "r1", "r2", "r3", "collection")}
    with app.state.session_factory() as session, session.begin():
        p1 = Process(id=ids["p1"], numero_cnj="00000010020268090001")
        p2 = Process(id=ids["p2"], numero_cnj="00000020020268090001")
        session.add_all([p1, p2])
        session.flush()

        representations = [
            Representation(
                id=ids["r1"],
                process_id=p1.id,
                source="synthetic",
                tribunal="TJGO",
                source_id="overview-p1-r1",
                last_observed_at=OBSERVED_AT,
            ),
            Representation(
                id=ids["r2"],
                process_id=p1.id,
                source="synthetic",
                tribunal="TJGO",
                source_id="overview-p1-r2",
                last_observed_at=OBSERVED_AT.replace(day=2),
            ),
            Representation(
                id=ids["r3"],
                process_id=p2.id,
                source="synthetic",
                tribunal="TJGO",
                source_id="overview-p2-r1",
                last_observed_at=OBSERVED_AT.replace(day=3),
            ),
        ]
        session.add_all(representations)
        session.flush()
        session.add_all(
            [
                RepresentationSubject(
                    representation_id=ids["r1"],
                    ordinal=1,
                    subject_code="4968",
                    subject_name="Crédito rural",
                ),
                RepresentationSubject(
                    representation_id=ids["r1"],
                    ordinal=2,
                    subject_code="4969",
                    subject_name="Penhora",
                ),
                RepresentationSubject(
                    representation_id=ids["r2"],
                    ordinal=1,
                    subject_code="4968",
                    subject_name="Crédito rural",
                ),
                RepresentationSubject(
                    representation_id=ids["r3"],
                    ordinal=1,
                    subject_code="4968",
                    subject_name="Crédito rural",
                ),
            ]
        )
        session.add(
            ProcessTriage(
                process_id=p2.id,
                decision="discarded",
                rural_link="unconfirmed",
                note="",
                version=1,
                updated_at=OBSERVED_AT,
            )
        )

        session.add(
            ProcessWatchlistEntry(
                process_id=p1.id,
                active=True,
                included_at=OBSERVED_AT,
                next_run_at=OBSERVED_AT,
            )
        )
        session.flush()
        cycle = ProcessWatchCycle(
            process_id=p1.id,
            cycle_number=1,
            started_at=OBSERVED_AT,
        )
        session.add(cycle)
        session.flush()
        session.add_all(
            [
                ProcessNews(
                    process_id=p1.id,
                    representation_id=ids["r1"],
                    watch_cycle_id=cycle.id,
                    category="NEW_OBSERVATION",
                    status="pending",
                    identity_key=f"overview-observation-{index}",
                    source_date_status="missing",
                    first_observed_at=OBSERVED_AT,
                    evidence={"source": "fixture"},
                    provenance="ingestion",
                )
                for index in (1, 2)
            ]
        )

        rule_job = Job(
            job_type="reprocess_rules",
            mode="demo",
            source="synthetic",
            tribunal="TJGO",
            operation_key="a" * 64,
            parameters_snapshot={"rule_ids": ["sinal.penhora", "sinal.leilao"]},
            status="completed",
        )
        session.add(rule_job)
        session.flush()
        signals = [
            (ids["p1"], ids["r1"], "sinal.penhora", "1.0.0", "penhora-current-a"),
            (ids["p1"], ids["r2"], "sinal.penhora", "1.0.0", "penhora-current-b"),
            (ids["p1"], ids["r1"], "sinal.penhora", "0.9.0", "penhora-old"),
            (ids["p2"], ids["r3"], "sinal.leilao", "1.0.0", "leilao-current"),
        ]
        session.add_all(
            [
                ProcessSignal(
                    process_id=process_id,
                    representation_id=representation_id,
                    evidence_kind="process_attribute",
                    evidence_id=uuid4(),
                    rule_id=rule_id,
                    rule_version=rule_version,
                    environment="demo",
                    enablement_snapshot={"demo": {"enabled": True}},
                    category=rule_id.removeprefix("sinal."),
                    explanation="fixture local",
                    result_fingerprint=sha256(fingerprint.encode()).hexdigest(),
                    created_run_id=rule_job.id,
                    published_run_id=rule_job.id,
                    is_published=True,
                    is_current=True,
                    evidence_stale=False,
                    evaluated_at=OBSERVED_AT,
                )
                for (process_id, representation_id, rule_id, rule_version, fingerprint) in signals
            ]
        )

        collection = Collection(
            id=ids["collection"],
            mode="demo",
            resolved_criteria={"preset_id": "rural.credito_contratos"},
            criteria_sha256="b" * 64,
        )
        session.add(collection)
        session.flush()
        session.add(
            Job(
                collection_id=collection.id,
                job_type="discovery",
                mode="demo",
                source="synthetic",
                tribunal="TJGO",
                operation_key="c" * 64,
                parameters_snapshot={"query": {"preset_id": "rural.credito_contratos"}},
                status="completed",
            )
        )
    return ids


def test_overview_counts_distinct_entities_and_current_signal_versions(
    overview_runtime: tuple[FastAPI, TestClient],
    overview_seed: dict[str, UUID],
) -> None:
    _app, client = overview_runtime
    response = client.get("/api/v1/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["data_source"] == "synthetic"
    assert body["generated_at"]
    assert body["processes"] == {"value": 2, "unit": "processes"}
    assert body["representations"] == {"value": 3, "unit": "representations"}
    assert body["triage"] == {
        "pending": {"value": 1, "unit": "processes"},
        "relevant": {"value": 0, "unit": "processes"},
        "discarded": {"value": 1, "unit": "processes"},
    }
    assert body["followed_processes"] == {"value": 1, "unit": "processes"}
    assert body["pending_news"] == {"value": 2, "unit": "occurrences"}
    assert body["current_signals"] == [
        {"category": "leilao", "processes": {"value": 1, "unit": "processes"}},
        {"category": "penhora", "processes": {"value": 1, "unit": "processes"}},
    ]
    assert body["themes"] == [
        {
            "subject_code": "4968",
            "subject_name": "Crédito rural",
            "processes": {"value": 2, "unit": "processes"},
        },
        {
            "subject_code": "4969",
            "subject_name": "Penhora",
            "processes": {"value": 1, "unit": "processes"},
        },
    ]
    assert sum(item["processes"]["value"] for item in body["themes"]) > body["processes"]["value"]
    assert body["latest_observation_at"] == "2026-10-03T12:00:00Z"
    assert body["latest_collections"][0]["collection_id"] == str(overview_seed["collection"])


def test_overview_process_and_news_drilldowns_use_the_same_resolved_cut(
    overview_runtime: tuple[FastAPI, TestClient],
    overview_seed: dict[str, UUID],
) -> None:
    _app, client = overview_runtime
    cut = {
        "subject_code": "4968",
        "subject_name_exact": "Crédito rural",
        "decision": "pending",
    }
    overview = client.get("/api/v1/overview", params=cut)
    processes = client.get("/api/v1/processes", params=cut)
    news = client.get("/api/v1/news", params={**cut, "status": "pending"})

    assert overview.status_code == processes.status_code == news.status_code == 200
    assert overview.json()["filters"]["subject_code"] == "4968"
    assert overview.json()["filters"]["subject_name_exact"] == "Crédito rural"
    assert overview.json()["filters"]["decision"] == "pending"
    assert overview.json()["processes"]["value"] == processes.json()["total"] == 1
    assert overview.json()["representations"]["value"] == 2
    assert overview.json()["pending_news"]["value"] == news.json()["total"] == 2
    assert overview.json()["themes"] == [
        {
            "subject_code": "4968",
            "subject_name": "Crédito rural",
            "processes": {"value": 1, "unit": "processes"},
        },
        {
            "subject_code": "4969",
            "subject_name": "Penhora",
            "processes": {"value": 1, "unit": "processes"},
        },
    ]

    signal_cut = client.get("/api/v1/overview", params={"signal_category": "penhora"})
    signal_list = client.get("/api/v1/processes", params={"signal_category": "penhora"})
    assert signal_cut.status_code == signal_list.status_code == 200
    assert signal_cut.json()["processes"]["value"] == signal_list.json()["total"] == 1
    assert signal_cut.json()["current_signals"] == [
        {"category": "penhora", "processes": {"value": 1, "unit": "processes"}}
    ]


def test_overview_empty_base_returns_real_zeroes_and_no_database_error_is_zero(
    overview_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = overview_runtime
    empty = client.get("/api/v1/overview")
    assert empty.status_code == 200
    assert empty.json()["processes"]["value"] == 0
    assert empty.json()["representations"]["value"] == 0
    assert empty.json()["triage"]["pending"]["value"] == 0
    assert empty.json()["themes"] == []
    assert empty.json()["current_signals"] == []

    def unavailable_session_factory() -> None:
        raise OperationalError("SELECT overview", {}, OSError("offline"))

    app.state.session_factory = unavailable_session_factory
    unavailable = client.get("/api/v1/overview")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "database_unavailable"
    assert "offline" not in unavailable.text


def test_overview_uses_one_read_only_snapshot_during_concurrent_subject_update(
    overview_runtime: tuple[FastAPI, TestClient],
    overview_seed: dict[str, UUID],
) -> None:
    app, client = overview_runtime
    inserted = False
    inserting_concurrent_subject = False
    statements: list[str] = []

    def capture_and_insert(
        connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal inserted, inserting_concurrent_subject
        if inserting_concurrent_subject:
            return
        normalized = statement.lstrip().lower()
        statements.append(normalized)
        if not inserted and "transaction_timestamp()" in normalized:
            inserted = True
            inserting_concurrent_subject = True
            try:
                with Session(app.state.database_engine) as writer, writer.begin():
                    writer.add(
                        RepresentationSubject(
                            representation_id=overview_seed["r1"],
                            ordinal=3,
                            subject_code="7000",
                            subject_name="Tema inserido durante a leitura",
                        )
                    )
            finally:
                inserting_concurrent_subject = False

    event.listen(app.state.database_engine, "after_cursor_execute", capture_and_insert)
    try:
        response = client.get("/api/v1/overview")
    finally:
        event.remove(app.state.database_engine, "after_cursor_execute", capture_and_insert)

    assert response.status_code == 200
    assert inserted
    assert all(not statement.startswith(("insert", "update", "delete")) for statement in statements)
    assert "7000" not in {item["subject_code"] for item in response.json()["themes"]}
    with Session(app.state.database_engine) as reader:
        assert (
            reader.scalar(
                select(RepresentationSubject.id).where(RepresentationSubject.subject_code == "7000")
            )
            is not None
        )


def test_overview_query_plans_with_a_controlled_local_volume(
    overview_runtime: tuple[FastAPI, TestClient],
    overview_seed: dict[str, UUID],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, _client = overview_runtime
    with app.state.session_factory() as session, session.begin():
        for index in range(100):
            process = Process(numero_cnj=f"{index + 100:020d}")
            session.add(process)
            session.flush()
            representation = Representation(
                process_id=process.id,
                source="synthetic",
                tribunal="TJGO",
                source_id=f"overview-volume-{index}",
                last_observed_at=OBSERVED_AT,
            )
            session.add(representation)
            session.flush()
            session.add(
                RepresentationSubject(
                    representation_id=representation.id,
                    ordinal=1,
                    subject_code="4968",
                    subject_name="Crédito rural",
                )
            )

    selected = _selected_processes(ProcessFilters(), "demo")
    statements = {
        "statistics": _overview_statistics_statement(selected),
        "signals": _signal_distribution_statement(selected, "demo"),
        "themes": _theme_distribution_statement(selected),
        "latest_collections": _latest_collections_statement(),
    }
    with app.state.database_engine.connect() as connection:
        for label, statement in statements.items():
            compiled = statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
            plan = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {compiled}"
            ).scalar_one()
            assert isinstance(plan, list) and plan and "Plan" in plan[0]
            root = plan[0]["Plan"]
            elapsed_ms = plan[0]["Execution Time"]
            assert elapsed_ms >= 0
            print(
                f"{label}: {root['Node Type']}, rows={root.get('Actual Rows', 0)}, "
                f"execution_ms={elapsed_ms:.3f}"
            )

    output = capsys.readouterr().out
    assert "statistics:" in output
    assert "signals:" in output
    assert "themes:" in output
    assert "latest_collections:" in output
    print(output, end="")
