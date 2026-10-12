"""PostgreSQL and API acceptance for multitribunal groups and product filters."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    ExecutionGroup,
    ExecutionGroupMember,
    Job,
    JobCheckpoint,
    Process,
    ProcessWatchlistEntry,
    Representation,
    SavedSearch,
    SavedSearchVersion,
    ScheduleDispatch,
)
from agrojud.services.scheduling import DailyScheduler
from agrojud.sources.tribunals import TRIBUNAL_ALLOWLIST

_PRESET = "rural.credito_contratos"


@pytest.fixture
def multitribunal_runtime(
    scratch_database_url: URL,
) -> Iterator[tuple[FastAPI, TestClient]]:
    database_url = scratch_database_url.render_as_string(hide_password=False)
    engine = make_engine(database_url)
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="demo",
        database_url=database_url,
    )
    app = create_app(settings, engine)
    with TestClient(app) as client:
        yield app, client
    engine.dispose()


def _discovery_group_body(tribunals: list[str], *, preset_id: str = _PRESET) -> dict[str, object]:
    return {
        "kind": "discovery",
        "criteria": {
            "preset_id": preset_id,
            "filed_from": "2025-01-01",
            "filed_through": "2025-02-01",
            "page_size": 100,
            "hit_budget": 200,
        },
        "tribunals": tribunals,
    }


def _saved_search_body(
    *, enabled: bool, tribunals: list[str], filed_from: str = "2025-01-01"
) -> dict[str, object]:
    return {
        "name": "Crédito rural multitribunal",
        "preset_id": _PRESET,
        "window_mode": "fixed",
        "filters": {
            "tribunals": tribunals,
            "filed_from": filed_from,
            "filed_through": "2025-02-01",
            "page_size": 100,
            "hit_budget": 200,
        },
        "enabled": enabled,
    }


def test_availability_api_lists_all_and_groups_accept_every_allowlisted_endpoint(
    multitribunal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = multitribunal_runtime
    availability = client.get("/api/v1/tribunal-availability", params={"preset_id": _PRESET})

    assert availability.status_code == 200
    body = availability.json()
    enabled = {item["tribunal"] for item in body["tribunals"] if item["enabled"]}
    expected = {endpoint.canonical_tribunal for endpoint in TRIBUNAL_ALLOWLIST.values()}
    assert enabled == expected
    assert len(body["tribunals"]) == 33
    assert body["selection_enabled"] is True
    assert body["matrix_sha256"] is None
    assert all(item["reason"] is None for item in body["tribunals"])
    assert all(item["s1_s2_state"] == "NOT_REQUIRED" for item in body["tribunals"])

    single = client.post(
        "/api/v1/job-groups",
        json=_discovery_group_body(["TRF6"]),
    )
    unknown = client.post(
        "/api/v1/job-groups",
        json=_discovery_group_body(["TJOUTRO"]),
    )
    assert single.status_code == 202, single.text
    assert single.json()["member_count"] == 1
    assert single.json()["members"][0]["tribunal"] == "TRF6"
    assert unknown.status_code == 422

    national = client.post(
        "/api/v1/job-groups",
        json=_discovery_group_body(
            [endpoint.canonical_tribunal for endpoint in TRIBUNAL_ALLOWLIST.values()]
        ),
    )
    assert national.status_code == 202, national.text
    assert national.json()["member_count"] == 33
    assert {member["tribunal"] for member in national.json()["members"]} == expected
    assert len({member["job_id"] for member in national.json()["members"]}) == 33
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionGroup)) == 2
        assert session.scalar(select(func.count()).select_from(Job)) == 33
        assert {job.tribunal for job in session.scalars(select(Job)).all()} == expected


def test_group_children_keep_independent_jobs_coverage_and_checkpoints_after_partial_failure(
    multitribunal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = multitribunal_runtime
    created = client.post(
        "/api/v1/job-groups",
        json=_discovery_group_body(["TJMT", "TJMS"]),
    )

    assert created.status_code == 202, created.text
    group = created.json()
    assert group["state"] == "queued"
    assert group["member_count"] == 2
    members = {member["tribunal"]: member for member in group["members"]}
    assert set(members) == {"TJMT", "TJMS"}
    job_ids = {member["job_id"] for member in members.values()}
    assert len(job_ids) == 2
    assert all(member["state"] == "QUEUED" for member in members.values())

    completed_at = datetime(2026, 10, 12, 12, tzinfo=UTC)
    failed_at = completed_at + timedelta(minutes=2)
    with Session(app.state.database_engine) as session, session.begin():
        jobs = {
            job.tribunal: job
            for job in session.scalars(select(Job).where(Job.id.in_(job_ids))).all()
        }
        assert set(jobs) == {"TJMT", "TJMS"}
        assert len({job.collection_id for job in jobs.values()}) == 2
        persisted_members = session.scalars(
            select(ExecutionGroupMember).where(ExecutionGroupMember.group_id == UUID(group["id"]))
        ).all()
        assert {member.tribunal for member in persisted_members} == {"TJMT", "TJMS"}
        checkpoints = {
            checkpoint.job_id: checkpoint
            for checkpoint in session.scalars(
                select(JobCheckpoint).where(JobCheckpoint.job_id.in_(job_ids))
            ).all()
        }
        assert set(checkpoints) == {job.id for job in jobs.values()}

        successful = jobs["TJMT"]
        successful.status = "completed"
        successful.coverage = {
            "hits_confirmed": 4,
            "pages_confirmed": 2,
            "valid_hits": 4,
            "rejected_hits": 0,
            "new": 3,
            "updated": 1,
            "unchanged": 0,
            "coverage": "complete",
        }
        successful.finished_at = completed_at
        successful.updated_at = completed_at
        checkpoint = checkpoints[successful.id]
        checkpoint.cursor = ["successful-cursor"]
        checkpoint.next_page = 3
        checkpoint.revision = 2
        checkpoint.updated_at = completed_at

        failed = jobs["TJMS"]
        failed.status = "failed"
        failed.reason = "source_timeout"
        failed.cursor_invalid = True
        failed.coverage = {
            "hits_confirmed": 2,
            "pages_confirmed": 1,
            "valid_hits": 2,
            "rejected_hits": 0,
            "new": 2,
            "updated": 0,
            "unchanged": 0,
            "coverage": "partial",
        }
        failed.finished_at = failed_at
        failed.updated_at = failed_at
        checkpoint = checkpoints[failed.id]
        checkpoint.cursor = ["failed-cursor"]
        checkpoint.next_page = 2
        checkpoint.revision = 1
        checkpoint.updated_at = failed_at

    detail = client.get(f"/api/v1/job-groups/{group['id']}")
    assert detail.status_code == 200, detail.text
    result = detail.json()
    assert result["state"] == "partial"
    assert result["completed_count"] == 1
    observed = {member["tribunal"]: member for member in result["members"]}
    assert observed["TJMT"]["state"] == "COMPLETED"
    assert observed["TJMT"]["coverage"]["new"] == 3
    assert observed["TJMT"]["next_page"] == 3
    assert observed["TJMT"]["checkpoint_revision"] == 2
    assert observed["TJMS"]["state"] == "FAILED"
    assert observed["TJMS"]["reason"] == "source_timeout"
    assert observed["TJMS"]["next_page"] == 2
    assert observed["TJMS"]["checkpoint_revision"] == 1
    with Session(app.state.database_engine) as session:
        persisted_group = session.get(ExecutionGroup, UUID(group["id"]))
        assert persisted_group is not None and persisted_group.state == "partial"

    restarted = client.post(f"/api/v1/jobs/{members['TJMS']['job_id']}/restart-scan")
    assert restarted.status_code == 202, restarted.text
    restarted_id = restarted.json()["job_id"]
    assert restarted_id != members["TJMS"]["job_id"]
    after_restart = client.get(f"/api/v1/job-groups/{group['id']}").json()
    after_members = {member["tribunal"]: member for member in after_restart["members"]}
    assert after_restart["state"] == "queued"
    assert after_members["TJMT"]["job_id"] == members["TJMT"]["job_id"]
    assert after_members["TJMT"]["next_page"] == 3
    assert after_members["TJMS"]["job_id"] == restarted_id
    assert after_members["TJMS"]["next_page"] == 1
    with Session(app.state.database_engine) as session:
        old_failed_checkpoint = session.scalar(
            select(JobCheckpoint).where(JobCheckpoint.job_id == UUID(members["TJMS"]["job_id"]))
        )
        new_checkpoint = session.scalar(
            select(JobCheckpoint).where(JobCheckpoint.job_id == UUID(restarted_id))
        )
        persisted_group = session.get(ExecutionGroup, UUID(group["id"]))
        assert old_failed_checkpoint is not None and old_failed_checkpoint.next_page == 2
        assert new_checkpoint is not None and new_checkpoint.next_page == 1
        assert persisted_group is not None and persisted_group.state == "queued"


def test_legacy_saved_revision_runs_as_tjgo_without_rewriting_its_snapshot(
    multitribunal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = multitribunal_runtime
    saved = client.post(
        "/api/v1/saved-searches",
        json=_saved_search_body(enabled=False, tribunals=["TJGO"]),
    )
    assert saved.status_code == 201, saved.text
    search_id = UUID(saved.json()["id"])
    with Session(app.state.database_engine) as session, session.begin():
        original = session.scalar(
            select(SavedSearchVersion).where(
                SavedSearchVersion.saved_search_id == search_id,
                SavedSearchVersion.version == 1,
            )
        )
        search = session.get(SavedSearch, search_id)
        assert original is not None and search is not None
        legacy_filters = dict(original.filters)
        legacy_filters.pop("tribunals")
        session.add(
            SavedSearchVersion(
                saved_search_id=search_id,
                version=2,
                name=original.name,
                preset_id=original.preset_id,
                preset_version=original.preset_version,
                window_mode=original.window_mode,
                filters=legacy_filters,
                catalog_snapshot=original.catalog_snapshot,
            )
        )
        search.current_version = 2

    listed = client.get("/api/v1/saved-searches")
    assert listed.status_code == 200, listed.text
    listed_search = next(item for item in listed.json() if item["id"] == str(search_id))
    assert listed_search["filters"]["tribunals"] == ["TJGO"]

    run = client.post(f"/api/v1/saved-searches/{search_id}/run")
    assert run.status_code == 202, run.text
    assert run.json()["member_count"] == 1
    assert run.json()["members"][0]["tribunal"] == "TJGO"
    assert run.json()["members"][0]["job_id"] is not None
    with Session(app.state.database_engine) as session:
        revision = session.scalar(
            select(SavedSearchVersion).where(
                SavedSearchVersion.saved_search_id == search_id,
                SavedSearchVersion.version == 2,
            )
        )
        assert revision is not None
        assert "tribunals" not in revision.filters


def test_saved_revision_and_daily_dispatch_preserve_group_snapshots_and_coalesce_active_target(
    multitribunal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = multitribunal_runtime
    saved = client.post(
        "/api/v1/saved-searches",
        json=_saved_search_body(enabled=False, tribunals=["TJMT", "TJMS"]),
    )
    assert saved.status_code == 201, saved.text
    search_id = saved.json()["id"]
    assert saved.json()["filters"]["tribunals"] == ["TJMT", "TJMS"]
    with Session(app.state.database_engine) as session:
        first_revision = session.scalar(
            select(SavedSearchVersion).where(SavedSearchVersion.saved_search_id == UUID(search_id))
        )
        assert first_revision is not None
        assert first_revision.filters["tribunals"] == ["TJMT", "TJMS"]

    manual = client.post(f"/api/v1/saved-searches/{search_id}/run")
    assert manual.status_code == 202, manual.text
    assert manual.json()["kind"] == "discovery"
    assert {member["tribunal"] for member in manual.json()["members"]} == {"TJMT", "TJMS"}
    repeated_manual = client.post(f"/api/v1/saved-searches/{search_id}/run")
    assert repeated_manual.status_code == 202, repeated_manual.text
    assert repeated_manual.json()["id"] == manual.json()["id"]

    revised = client.patch(
        f"/api/v1/saved-searches/{search_id}",
        json={
            "filters": {
                "tribunals": ["TJMS"],
                "filed_from": "2025-01-01",
                "filed_through": "2025-02-01",
                "page_size": 100,
                "hit_budget": 200,
            }
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["version"] == 2
    with Session(app.state.database_engine) as session:
        revisions = session.scalars(
            select(SavedSearchVersion)
            .where(SavedSearchVersion.saved_search_id == UUID(search_id))
            .order_by(SavedSearchVersion.version)
        ).all()
        assert [revision.filters["tribunals"] for revision in revisions] == [
            ["TJMT", "TJMS"],
            ["TJMS"],
        ]

    scheduled = client.post(
        "/api/v1/saved-searches",
        json=_saved_search_body(enabled=True, tribunals=["TJMT", "TJMS"], filed_from="2024-01-01"),
    )
    assert scheduled.status_code == 201, scheduled.text
    scheduled_id = UUID(scheduled.json()["id"])
    scheduler = DailyScheduler(
        app.state.session_factory,
        app.state.jobs,
        mode="demo",
        source="synthetic",
        settings=app.state.settings,
        execution_groups=app.state.execution_groups,
    )
    first_run = datetime(2026, 10, 12, 13, tzinfo=UTC)
    with Session(app.state.database_engine) as session, session.begin():
        search = session.get(SavedSearch, scheduled_id)
        assert search is not None
        search.next_run_at = first_run - timedelta(minutes=1)
    assert scheduler.run_due(reference_time=first_run) == 1

    with Session(app.state.database_engine) as session:
        dispatches = session.scalars(
            select(ScheduleDispatch)
            .where(ScheduleDispatch.saved_search_id == scheduled_id)
            .order_by(ScheduleDispatch.scheduled_for_date)
        ).all()
        assert len(dispatches) == 1
        assert dispatches[0].status == "enqueued"
        assert dispatches[0].group_id is not None
        assert dispatches[0].job_id is None
        group_count = session.scalar(select(func.count()).select_from(ExecutionGroup))

    second_run = first_run + timedelta(days=1)
    with Session(app.state.database_engine) as session, session.begin():
        search = session.get(SavedSearch, scheduled_id)
        assert search is not None
        search.next_run_at = second_run - timedelta(minutes=1)
    assert scheduler.run_due(reference_time=second_run) == 1

    with Session(app.state.database_engine) as session:
        dispatches = session.scalars(
            select(ScheduleDispatch)
            .where(ScheduleDispatch.saved_search_id == scheduled_id)
            .order_by(ScheduleDispatch.scheduled_for_date)
        ).all()
        assert len(dispatches) == 2
        assert dispatches[0].status == "enqueued" and dispatches[0].group_id is not None
        assert dispatches[1].status == "pending" and dispatches[1].group_id is None
        assert session.scalar(select(func.count()).select_from(ExecutionGroup)) == group_count


def test_watch_refresh_reuses_active_group_and_deduplicates_same_tribunal_representations(
    multitribunal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = multitribunal_runtime
    process_id = UUID("33333333-3333-4333-8333-333333333333")
    observed_at = datetime(2026, 10, 12, 12, tzinfo=UTC)
    with Session(app.state.database_engine) as session, session.begin():
        process = Process(id=process_id, numero_cnj="00000010020268090001")
        session.add(process)
        session.flush()
        session.add_all(
            [
                Representation(
                    process_id=process_id,
                    source="synthetic",
                    tribunal=tribunal,
                    source_id=source_id,
                    last_observed_at=observed_at,
                )
                for tribunal, source_id in (
                    ("TJMT", "watch-tjmt-one"),
                    ("TJMT", "watch-tjmt-two"),
                    ("TJMS", "watch-tjms-one"),
                )
            ]
        )
        session.add(
            ProcessWatchlistEntry(
                process_id=process_id,
                active=True,
                included_at=observed_at,
                next_run_at=observed_at + timedelta(days=1),
            )
        )

    first = client.post(f"/api/v1/processes/{process_id}/refresh")
    second = client.post(f"/api/v1/processes/{process_id}/refresh")
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert {member["tribunal"] for member in second.json()["members"]} == {"TJMT", "TJMS"}

    scheduler = DailyScheduler(
        app.state.session_factory,
        app.state.jobs,
        mode="demo",
        source="synthetic",
        settings=app.state.settings,
        execution_groups=app.state.execution_groups,
    )
    scheduled_at = observed_at + timedelta(days=1, hours=1)
    assert scheduler.run_due(reference_time=scheduled_at) == 1
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionGroup)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        assert session.scalar(select(func.count()).select_from(ExecutionGroupMember)) == 2
        dispatch = session.scalar(
            select(ScheduleDispatch).where(ScheduleDispatch.process_id == process_id)
        )
        assert dispatch is not None
        assert dispatch.status == "pending"
        assert dispatch.group_id is None and dispatch.job_id is None
