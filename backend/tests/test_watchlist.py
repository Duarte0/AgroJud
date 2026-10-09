"""PostgreSQL and HTTP acceptance tests for SPEC-015 manual process tracking."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config, upgrade_database
from agrojud.db.models import (
    Job,
    Process,
    ProcessTriage,
    ProcessWatchlistEntry,
    ProcessWatchlistHistory,
    Representation,
)
from agrojud.services.collection import CollectionJobHandler, build_collection_job_handler
from agrojud.services.job_worker import LeasedWorker
from agrojud.sources.contracts import SourceError, SourceErrorCode, SourcePage

CNJ_A = "00000010020268090001"
CNJ_B = "00000020020268090002"
CNJ_C = "00000030020268090003"


@pytest.fixture
def watch_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    engine = create_test_engine(scratch_database_url)
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="demo",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    app = create_app(settings, engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


def test_watchlist_migration_upgrades_0007_without_losing_local_processes(
    scratch_database_url: URL,
) -> None:
    engine = create_test_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            command.upgrade(make_alembic_config(connection), "20261009_0007")
        with Session(engine) as session, session.begin():
            process = Process(numero_cnj=CNJ_A)
            session.add(process)
            session.flush()
            process_id = process.id

        with engine.begin() as connection:
            upgrade_database(connection)

        with Session(engine) as session:
            assert session.get(Process, process_id) is not None
        tables = set(inspect(engine).get_table_names())
        assert {"process_watchlist_entries", "process_watchlist_history"}.issubset(tables)
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "20261009_0009"
    finally:
        engine.dispose()


def test_include_remove_reinclude_are_idempotent_and_audited(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = watch_runtime
    process_id = create_process(app, CNJ_A)
    triage = client.patch(
        f"/api/v1/processes/{process_id}/triage",
        json={
            "expected_version": 0,
            "decision": "discarded",
            "rural_link": "confirmed",
            "note": "Revisado antes do acompanhamento.",
        },
    )
    assert triage.status_code == 200

    included = client.put(f"/api/v1/processes/{process_id}/watch")
    repeated = client.put(f"/api/v1/processes/{process_id}/watch")
    assert included.status_code == repeated.status_code == 200
    assert included.json()["active"] is True
    assert repeated.json()["history"] == included.json()["history"]
    assert [item["action"] for item in repeated.json()["history"]] == ["included"]
    first_included_at = included.json()["included_at"]

    removed = client.delete(f"/api/v1/processes/{process_id}/watch")
    repeated_removal = client.delete(f"/api/v1/processes/{process_id}/watch")
    assert removed.status_code == repeated_removal.status_code == 200
    assert removed.json()["active"] is False
    assert removed.json()["removed_at"] is not None
    assert [item["action"] for item in repeated_removal.json()["history"]] == [
        "included",
        "removed",
    ]

    reintroduced = client.put(f"/api/v1/processes/{process_id}/watch")
    assert reintroduced.status_code == 200
    assert reintroduced.json()["active"] is True
    assert reintroduced.json()["removed_at"] is None
    assert reintroduced.json()["included_at"] >= first_included_at
    assert [item["action"] for item in reintroduced.json()["history"]] == [
        "included",
        "removed",
        "included",
    ]
    assert client.get("/api/v1/watchlist").json()["total"] == 1
    assert client.get(f"/api/v1/processes/{process_id}").json()["triage"] == triage.json()
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(ProcessWatchlistEntry)) == 1
        assert session.scalar(select(func.count()).select_from(ProcessWatchlistHistory)) == 3


def test_concurrent_inclusions_create_one_entry_and_one_effective_event(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, _client = watch_runtime
    process_id = create_process(app, CNJ_A)
    start = Barrier(2)

    def include() -> dict[str, object]:
        with TestClient(app, raise_server_exceptions=False) as client:
            start.wait(timeout=5)
            response = client.put(f"/api/v1/processes/{process_id}/watch")
            assert response.status_code == 200
            return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: include(), range(2)))

    assert all(result["active"] is True for result in results)
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(ProcessWatchlistEntry)) == 1
        assert session.scalar(select(func.count()).select_from(ProcessWatchlistHistory)) == 1


def test_refresh_requires_a_local_active_watch_and_deduplicates_each_target(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = watch_runtime
    first_id = create_process(app, CNJ_A)
    second_id = create_process(app, CNJ_B)

    assert client.post(f"/api/v1/processes/{uuid4()}/refresh").status_code == 404
    assert client.post(f"/api/v1/processes/{first_id}/refresh").status_code == 409
    assert (
        client.post(
            "/api/v1/jobs",
            json={"kind": "refresh_number", "criteria": {"process_number": CNJ_C}},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/jobs",
            json={"kind": "refresh_number", "criteria": {"process_number": CNJ_A}},
        ).status_code
        == 409
    )

    assert client.put(f"/api/v1/processes/{first_id}/watch").status_code == 200
    assert client.put(f"/api/v1/processes/{second_id}/watch").status_code == 200
    first = client.post(f"/api/v1/processes/{first_id}/refresh")
    repeated = client.post(f"/api/v1/processes/{first_id}/refresh")
    second = client.post(f"/api/v1/processes/{second_id}/refresh")
    assert first.status_code == repeated.status_code == second.status_code == 202
    assert first.json()["job_id"] == repeated.json()["job_id"]
    assert first.json()["reused"] is False
    assert repeated.json()["reused"] is True
    assert second.json()["job_id"] != first.json()["job_id"]

    detail = client.get(f"/api/v1/jobs/{first.json()['job_id']}").json()
    assert detail["criteria"]["process_number"] == CNJ_A
    assert detail["criteria"]["filed_from"] is None
    assert detail["criteria"]["filed_to"] is None
    pending = client.get("/api/v1/watchlist").json()
    assert pending["total"] == 2
    assert all(item["last_refresh"]["state"] == "pending" for item in pending["items"])
    first_page = client.get("/api/v1/watchlist", params={"page": 1, "page_size": 1}).json()
    second_page = client.get("/api/v1/watchlist", params={"page": 2, "page_size": 1}).json()
    assert first_page["total"] == second_page["total"] == 2
    assert len(first_page["items"]) == len(second_page["items"]) == 1
    assert first_page["items"][0]["process_id"] != second_page["items"][0]["process_id"]


def test_removing_watch_does_not_cancel_an_accepted_refresh_or_change_triage(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = watch_runtime
    process_id = create_process(app, CNJ_A)
    add_representation(app, process_id)
    triage = client.patch(
        f"/api/v1/processes/{process_id}/triage",
        json={
            "expected_version": 0,
            "decision": "relevant",
            "note": "Manter como relevante.",
        },
    )
    assert triage.status_code == 200
    assert client.put(f"/api/v1/processes/{process_id}/watch").status_code == 200
    created = client.post(f"/api/v1/processes/{process_id}/refresh")
    assert created.status_code == 202

    removed = client.delete(f"/api/v1/processes/{process_id}/watch")
    assert removed.status_code == 200
    with Session(app.state.database_engine) as session:
        queued = session.get(Job, UUID(created.json()["job_id"]))
        assert queued is not None and queued.status == "queued"

    worker = LeasedWorker(
        app.state.jobs,
        worker_id="manual-watch-refresh-worker",
        handlers={"refresh_number": build_collection_job_handler(app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()

    state = client.get(f"/api/v1/processes/{process_id}/watch").json()
    assert state["active"] is False
    assert state["last_refresh"]["state"] == "found"
    assert state["last_refresh"]["checked_at"]
    detail = client.get(f"/api/v1/processes/{process_id}").json()
    assert detail["triage"] == triage.json()
    assert detail["representation_count"] >= 2
    assert client.get("/api/v1/watchlist").json()["total"] == 0

    with Session(app.state.database_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Representation)
                .where(Representation.process_id == process_id)
            )
            > 0
        )


def test_partial_refresh_is_not_reported_as_absent(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = watch_runtime
    process_id = create_process(app, CNJ_A)
    assert client.put(f"/api/v1/processes/{process_id}/watch").status_code == 200
    created = client.post(
        "/api/v1/jobs",
        json={
            "kind": "refresh_number",
            "criteria": {"process_number": CNJ_A, "hit_budget": 1},
        },
    )
    assert created.status_code == 202

    worker = LeasedWorker(
        app.state.jobs,
        worker_id="manual-watch-partial-worker",
        handlers={"refresh_number": build_collection_job_handler(app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()

    result = client.get(f"/api/v1/processes/{process_id}/watch").json()["last_refresh"]
    assert result["state"] == "partial"
    assert result["job_status"] == "partial"
    assert result["hit_count"] == 1


@pytest.mark.parametrize(
    ("source_error", "expected_state", "expected_job_status"),
    [
        (None, "absent_in_query", "completed"),
        (
            SourceError(SourceErrorCode.AUTHENTICATION, "Falha sintética de autenticação."),
            "failed",
            "failed",
        ),
        (
            SourceError(SourceErrorCode.RATE_LIMIT, "429 sintético.", retry_after="30"),
            "pending",
            "retry_wait",
        ),
    ],
)
def test_empty_failure_and_rate_limit_results_remain_distinct_and_preserve_local_rows(
    watch_runtime: tuple[FastAPI, TestClient],
    source_error: SourceError | None,
    expected_state: str,
    expected_job_status: str,
) -> None:
    app, client = watch_runtime
    process_id = create_process(app, CNJ_A)
    add_representation(app, process_id)
    assert client.put(f"/api/v1/processes/{process_id}/watch").status_code == 200
    created = client.post(f"/api/v1/processes/{process_id}/refresh")
    assert created.status_code == 202

    adapter = _ResultAdapter(source_error)
    worker = LeasedWorker(
        app.state.jobs,
        worker_id=f"manual-watch-{expected_job_status}-worker",
        handlers={"refresh_number": CollectionJobHandler(lambda _lease: adapter)},
        heartbeat_interval=1,
    )
    assert worker.run_once()

    state = client.get(f"/api/v1/processes/{process_id}/watch").json()["last_refresh"]
    assert state["state"] == expected_state
    assert state["job_status"] == expected_job_status
    assert state["checked_at"]
    with Session(app.state.database_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Representation)
                .where(Representation.process_id == process_id)
            )
            == 1
        )
        triage = session.get(ProcessTriage, process_id)
        assert triage is None


def test_watch_history_is_append_only(
    watch_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = watch_runtime
    process_id = create_process(app, CNJ_A)
    assert client.put(f"/api/v1/processes/{process_id}/watch").status_code == 200

    with pytest.raises(DBAPIError):
        with app.state.database_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM process_watchlist_history WHERE process_id = :process_id"),
                {"process_id": process_id},
            )

    assert client.get(f"/api/v1/processes/{process_id}/watch").json()["history"]


class _ResultAdapter:
    def __init__(self, error: SourceError | None) -> None:
        self.error = error

    def fetch_page(self, _query, _cursor=None, _page_size=100) -> SourcePage:
        if self.error is not None:
            raise self.error
        return SourcePage(
            hits=(),
            total_value=0,
            total_relation="eq",
            cursor_final=None,
            responded_at=datetime.now(UTC),
            raw_envelope={"hits": {"hits": [], "total": {"value": 0, "relation": "eq"}}},
        )

    def fetch_by_case_number(self, process_number, cursor=None, page_size=100) -> SourcePage:
        return self.fetch_page(None, cursor, page_size)


def create_test_engine(database_url: URL):
    return make_engine(database_url.render_as_string(hide_password=False))


def create_process(app: FastAPI, process_number: str) -> UUID:
    with app.state.session_factory() as session, session.begin():
        process = Process(numero_cnj=process_number)
        session.add(process)
        session.flush()
        return process.id


def add_representation(app: FastAPI, process_id: UUID) -> None:
    with app.state.session_factory() as session, session.begin():
        session.add(
            Representation(
                process_id=process_id,
                source="synthetic",
                tribunal="TJGO",
                source_id=f"existing-{process_id}",
                source_filed_at=datetime(2001, 1, 1, tzinfo=UTC),
            )
        )
