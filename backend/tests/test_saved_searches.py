"""PostgreSQL and HTTP acceptance coverage for SPEC-017 scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Job,
    Process,
    ProcessWatchlistEntry,
    SavedSearch,
    SavedSearchVersion,
    ScheduleDispatch,
)
from agrojud.services.saved_searches import request_for_revision
from agrojud.services.schedule_time import next_daily_occurrence, scheduled_instant
from agrojud.services.scheduling import DailyScheduler
from agrojud.sources.catalog import compile_preset

PRESET_ID = "rural.credito_contratos"


@pytest.fixture
def saved_search_runtime(
    scratch_database_url: URL,
) -> Iterator[tuple[FastAPI, TestClient]]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="demo",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    app = create_app(settings, engine)
    with TestClient(app) as client:
        yield app, client
    engine.dispose()


def fixed_search_body(
    name: str = "Busca de teste",
    *,
    enabled: bool = False,
    filed_from: str = "2025-01-01",
    filed_through: str = "2025-02-01",
) -> dict[str, object]:
    return {
        "name": name,
        "preset_id": PRESET_ID,
        "window_mode": "fixed",
        "filters": {
            "filed_from": filed_from,
            "filed_through": filed_through,
            "page_size": 100,
            "hit_budget": 200,
        },
        "enabled": enabled,
    }


def create_search(client: TestClient, **kwargs: object) -> dict[str, object]:
    response = client.post("/api/v1/saved-searches", json=fixed_search_body(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def make_scheduler(app: FastAPI) -> DailyScheduler:
    return DailyScheduler(
        app.state.session_factory,
        app.state.jobs,
        mode="demo",
        source="synthetic",
    )


def set_search_due(app: FastAPI, search_id: str, local_date: date) -> None:
    with Session(app.state.database_engine) as session, session.begin():
        session.execute(
            update(SavedSearch)
            .where(SavedSearch.id == UUID(search_id))
            .values(next_run_at=scheduled_instant(local_date))
        )


def test_saved_search_patch_appends_a_revision_and_first_enable_is_future(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    created = create_search(client)
    assert created["version"] == 1
    assert created["enabled"] is False
    assert created["next_run_at"] is None

    enabled = client.patch(f"/api/v1/saved-searches/{created['id']}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    next_run = datetime.fromisoformat(enabled.json()["next_run_at"])
    assert next_run > datetime.now(UTC)
    assert enabled.json()["version"] == 1

    changed = client.patch(
        f"/api/v1/saved-searches/{created['id']}",
        json={
            "name": "Busca revisada",
            "filters": {
                "filed_from": "2025-03-01",
                "filed_through": "2025-04-01",
                "page_size": 50,
                "hit_budget": 300,
            },
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    assert changed.json()["name"] == "Busca revisada"
    with Session(app.state.database_engine) as session:
        revisions = session.scalars(
            select(SavedSearchVersion)
            .where(SavedSearchVersion.saved_search_id == UUID(str(created["id"])))
            .order_by(SavedSearchVersion.version)
        ).all()
    assert [revision.version for revision in revisions] == [1, 2]
    assert revisions[0].name == "Busca de teste"
    assert revisions[0].filters["filed_from"] == "2025-01-01"

    with pytest.raises(DBAPIError):
        with Session(app.state.database_engine) as session, session.begin():
            session.execute(
                text("UPDATE saved_search_versions SET name = 'mutada' WHERE id = :id"),
                {"id": revisions[0].id},
            )


def test_manual_and_scheduled_saved_search_runs_reuse_one_active_job(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    search = create_search(client)
    manual = client.post(f"/api/v1/saved-searches/{search['id']}/run")
    assert manual.status_code == 202, manual.text
    manual_job_id = manual.json()["job_id"]

    enable = client.patch(f"/api/v1/saved-searches/{search['id']}", json={"enabled": True})
    assert enable.status_code == 200
    set_search_due(app, str(search["id"]), date(2026, 10, 9))
    now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
    assert make_scheduler(app).run_due(reference_time=now) == 1

    with Session(app.state.database_engine) as session:
        jobs = session.scalars(select(Job)).all()
        dispatch = session.scalar(select(ScheduleDispatch))
    assert len(jobs) == 1
    assert dispatch is not None and dispatch.status == "enqueued"
    assert str(dispatch.job_id) == manual_job_id
    assert dispatch.missed_from == date(2026, 10, 9)
    assert dispatch.missed_through == date(2026, 10, 9)
    assert make_scheduler(app).run_due(reference_time=now) == 0


def test_watch_enable_waits_for_daily_time_and_schedule_reuses_manual_refresh(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    with Session(app.state.database_engine) as session, session.begin():
        process = Process(numero_cnj="00000030020268090003")
        session.add(process)
        session.flush()
        process_id = process.id

    included = client.put(f"/api/v1/processes/{process_id}/watch")
    assert included.status_code == 200, included.text
    next_run = datetime.fromisoformat(included.json()["next_run_at"])
    assert next_run > datetime.now(UTC)
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0

    manual = client.post(f"/api/v1/processes/{process_id}/refresh")
    assert manual.status_code == 202, manual.text
    with Session(app.state.database_engine) as session, session.begin():
        session.execute(
            update(ProcessWatchlistEntry)
            .where(ProcessWatchlistEntry.process_id == process_id)
            .values(next_run_at=scheduled_instant(date(2026, 10, 4)))
        )
    now = datetime(2026, 10, 9, 15, tzinfo=UTC)
    assert make_scheduler(app).run_due(reference_time=now) == 1

    with Session(app.state.database_engine) as session:
        jobs = session.scalars(select(Job)).all()
        dispatch = session.scalar(select(ScheduleDispatch))
    assert len(jobs) == 1
    assert dispatch is not None and dispatch.status == "enqueued"
    assert str(dispatch.job_id) == manual.json()["job_id"]
    assert dispatch.missed_from == date(2026, 10, 4)
    status = client.get(f"/api/v1/processes/{process_id}/watch").json()
    assert status["last_schedule"]["status"] == "enqueued"
    assert status["next_run_at"] is not None


def test_restart_aggregates_missed_days_into_one_dispatch(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    search = create_search(client, enabled=True)
    set_search_due(app, str(search["id"]), date(2026, 10, 4))
    now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)

    assert make_scheduler(app).run_due(reference_time=now) == 1
    with Session(app.state.database_engine) as session:
        dispatches = session.scalars(select(ScheduleDispatch)).all()
        jobs = session.scalars(select(Job)).all()
        persisted = session.get(SavedSearch, UUID(str(search["id"])))
    assert len(dispatches) == 1
    assert len(jobs) == 1
    assert persisted is not None
    assert dispatches[0].scheduled_for_date == date(2026, 10, 9)
    assert dispatches[0].missed_from == date(2026, 10, 4)
    assert dispatches[0].missed_through == date(2026, 10, 9)
    assert persisted.next_run_at == next_daily_occurrence(now)


def test_changed_criteria_wait_for_existing_target_job_then_dispatch_once(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    search = create_search(client, enabled=True)
    old_job = client.post(f"/api/v1/saved-searches/{search['id']}/run")
    assert old_job.status_code == 202
    changed = client.patch(
        f"/api/v1/saved-searches/{search['id']}",
        json={
            "filters": {
                "filed_from": "2025-03-01",
                "filed_through": "2025-04-01",
                "page_size": 100,
                "hit_budget": 200,
            }
        },
    )
    assert changed.status_code == 200 and changed.json()["version"] == 2
    set_search_due(app, str(search["id"]), date(2026, 10, 9))
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    scheduler = make_scheduler(app)
    assert scheduler.run_due(reference_time=now) == 1

    with Session(app.state.database_engine) as session:
        dispatch = session.scalar(select(ScheduleDispatch))
        jobs = session.scalars(select(Job).order_by(Job.created_at)).all()
    assert dispatch is not None and dispatch.status == "pending"
    assert len(jobs) == 1 and str(jobs[0].id) == old_job.json()["job_id"]

    with Session(app.state.database_engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == UUID(old_job.json()["job_id"]))
            .values(status="completed", finished_at=now, updated_at=now)
        )
    assert scheduler.run_due(reference_time=now) == 1
    with Session(app.state.database_engine) as session:
        dispatch = session.scalar(select(ScheduleDispatch))
        jobs = session.scalars(select(Job).order_by(Job.created_at)).all()
    assert dispatch is not None and dispatch.status == "enqueued"
    assert dispatch.job_id is not None
    assert len(jobs) == 2
    assert jobs[1].status == "queued"
    assert jobs[1].parameters_snapshot["parameters"]["saved_search_version"] == 2


def test_disabling_search_cancels_pending_dispatch_without_stopping_active_job(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    search = create_search(client, enabled=True)
    active = client.post(f"/api/v1/saved-searches/{search['id']}/run")
    assert active.status_code == 202
    client.patch(
        f"/api/v1/saved-searches/{search['id']}",
        json={
            "filters": {
                "filed_from": "2025-03-01",
                "filed_through": "2025-04-01",
                "page_size": 100,
                "hit_budget": 200,
            }
        },
    )
    set_search_due(app, str(search["id"]), date(2026, 10, 9))
    scheduler = make_scheduler(app)
    scheduler.run_due(reference_time=datetime(2026, 10, 9, 12, tzinfo=UTC))

    disabled = client.patch(f"/api/v1/saved-searches/{search['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    with Session(app.state.database_engine) as session:
        dispatch = session.scalar(select(ScheduleDispatch))
        active_job = session.get(Job, UUID(active.json()["job_id"]))
        job_count = session.scalar(select(func.count()).select_from(Job))
    assert dispatch is not None and dispatch.status == "cancelled"
    assert active_job is not None and active_job.status == "queued"
    assert job_count == 1
    assert scheduler.run_due(reference_time=datetime(2026, 10, 10, 12, tzinfo=UTC)) == 0


def test_two_scheduler_sessions_reserve_the_same_due_search_once(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    search = create_search(client, enabled=True)
    set_search_due(app, str(search["id"]), date(2026, 10, 9))
    scheduler = make_scheduler(app)
    barrier = Barrier(2)
    now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)

    def reserve() -> int:
        barrier.wait(timeout=10)
        return scheduler.run_due(reference_time=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reserve(), range(2)))

    with Session(app.state.database_engine) as session:
        dispatch_count = session.scalar(select(func.count()).select_from(ScheduleDispatch))
        job_count = session.scalar(select(func.count()).select_from(Job))
    assert sorted(results) == [0, 1]
    assert dispatch_count == 1
    assert job_count == 1


def test_failure_after_enqueue_rolls_back_dispatch_job_and_next_time(
    saved_search_runtime: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = saved_search_runtime
    search = create_search(client, enabled=True)
    due = scheduled_instant(date(2026, 10, 4))
    set_search_due(app, str(search["id"]), date(2026, 10, 4))
    original_next_run = due
    original_enqueue = app.state.jobs.enqueue_in_session

    def enqueue_then_fail(session: Session, request: object) -> object:
        original_enqueue(session, request)
        raise RuntimeError("falha injetada antes do commit")

    monkeypatch.setattr(app.state.jobs, "enqueue_in_session", enqueue_then_fail)
    with pytest.raises(RuntimeError, match="falha injetada"):
        make_scheduler(app).run_due(reference_time=datetime(2026, 10, 9, 15, tzinfo=UTC))

    with Session(app.state.database_engine) as session:
        dispatch_count = session.scalar(select(func.count()).select_from(ScheduleDispatch))
        job_count = session.scalar(select(func.count()).select_from(Job))
        persisted = session.get(SavedSearch, UUID(str(search["id"])))
    assert dispatch_count == 0
    assert job_count == 0
    assert persisted is not None and persisted.next_run_at == original_next_run


def test_rolling_window_uses_calendar_months_and_new_runs_freeze_their_dates(
    saved_search_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = saved_search_runtime
    response = client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Janela móvel",
            "preset_id": PRESET_ID,
            "window_mode": "rolling_12_months",
            "filters": {"page_size": 100, "hit_budget": 200},
            "enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    with Session(app.state.database_engine) as session:
        revision = session.scalar(
            select(SavedSearchVersion).where(
                SavedSearchVersion.saved_search_id == UUID(response.json()["id"])
            )
        )
    assert revision is not None
    leap_day = compile_preset(
        PRESET_ID,
        environment="demo",
        reference_time=datetime(2024, 2, 29, 17, tzinfo=UTC),
    )
    assert leap_day.query.filed_from == date(2023, 2, 28)
    assert leap_day.query.filed_to == date(2024, 2, 29)

    first = request_for_revision(
        revision,
        search_id=revision.saved_search_id,
        mode="demo",
        source="synthetic",
        reference_time=datetime(2025, 3, 31, 15, tzinfo=UTC),
    )
    second = request_for_revision(
        revision,
        search_id=revision.saved_search_id,
        mode="demo",
        source="synthetic",
        reference_time=datetime(2025, 4, 1, 15, tzinfo=UTC),
    )
    assert first.resolved_query["filed_from"] == "2024-03-31"
    assert first.resolved_query["filed_to"] == "2025-03-31"
    assert second.resolved_query["filed_from"] == "2024-04-01"
    assert second.resolved_query["filed_to"] == "2025-04-01"

    first_result = app.state.jobs.enqueue(first)
    original_snapshot = app.state.jobs.inspect(first_result.job_id).parameters_snapshot
    with Session(app.state.database_engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == first_result.job_id)
            .values(status="failed", reason="test", finished_at=datetime.now(UTC))
        )
    assert app.state.jobs.resume(first_result.job_id) == first_result.job_id
    resumed_snapshot = app.state.jobs.inspect(first_result.job_id).parameters_snapshot
    assert resumed_snapshot == original_snapshot
    second_result = app.state.jobs.enqueue(second)
    assert second_result.job_id != first_result.job_id


def test_invalid_real_source_is_visible_and_never_enqueued(
    scratch_database_url: URL,
) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="real",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    app = create_app(settings, engine)
    with TestClient(app) as client:
        search = create_search(client, enabled=True)
        assert search["availability"]["enabled"] is False
        assert search["availability"]["reasons"]
        run = client.post(f"/api/v1/saved-searches/{search['id']}/run")
        assert run.status_code == 422
        set_search_due(app, str(search["id"]), date(2026, 10, 4))
        assert (
            DailyScheduler(
                app.state.session_factory, app.state.jobs, mode="real", source="datajud"
            ).run_due(reference_time=datetime(2026, 10, 9, 15, tzinfo=UTC))
            == 1
        )
        with Session(engine) as session:
            dispatch = session.scalar(select(ScheduleDispatch))
            jobs = session.scalar(select(func.count()).select_from(Job))
        assert dispatch is not None and dispatch.status == "blocked"
        assert dispatch.reason
        assert jobs == 0
    engine.dispose()
