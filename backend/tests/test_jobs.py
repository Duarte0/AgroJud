"""PostgreSQL concurrency and lifecycle evidence for SPEC-007."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config, upgrade_database
from agrojud.db.models import Collection, Job, JobCheckpoint
from agrojud.services.job_worker import JobExecutionContext, JobOutcome, LeasedWorker
from agrojud.services.jobs import (
    CheckpointRevisionConflict,
    EnqueueRequest,
    JobCancellationRequestedError,
    JobLease,
    JobService,
    LeaseLostError,
)

EXPECTED_JOB_TABLES = {
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
def migrated_engine(scratch_database_url: URL) -> Iterator[Engine]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    yield engine
    engine.dispose()


def make_request(*, budget: int = 100) -> EnqueueRequest:
    return EnqueueRequest(
        mode="demo",
        job_type="discovery",
        source="synthetic",
        tribunal="TJGO",
        resolved_query={"date_from": "2026-01-01", "date_to": "2026-02-01"},
        sort=["@timestamp", "id"],
        parameters={"budget": budget, "page_size": 100},
    )


def make_service(engine: Engine, *, lease_seconds: float = 120) -> JobService:
    sessions = sessionmaker(engine, expire_on_commit=False)
    return JobService(sessions, lease_duration=timedelta(seconds=lease_seconds))


def expire_lease(engine: Engine, job_id: UUID) -> None:
    with Session(engine) as session, session.begin():
        expired_at = session.scalar(select(func.clock_timestamp() - text("interval '1 second'")))
        session.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=expired_at))


def test_jobs_migration_upgrades_empty_and_spec006_database(scratch_database_url: URL) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            assert inspect(connection).get_table_names() == []
            command.upgrade(make_alembic_config(connection), "20261009_0003")
            assert "quarantine_rejections" in inspect(connection).get_table_names()

        with Session(engine) as session, session.begin():
            from agrojud.services.ingestion import create_collection

            prior_collection = create_collection(
                session,
                mode="demo",
                resolved_criteria={"prior": "spec006"},
            )
            prior_collection_id = prior_collection.id

        with engine.begin() as connection:
            upgrade_database(connection)
            assert set(inspect(connection).get_table_names()) == EXPECTED_JOB_TABLES

        with Session(engine) as session:
            assert session.get(Collection, prior_collection_id) is not None
    finally:
        engine.dispose()


def test_enqueue_coalesces_concurrent_equivalent_work_and_claims_once(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    start_enqueue = Barrier(2)

    def enqueue(budget: int) -> object:
        start_enqueue.wait(timeout=5)
        return jobs.enqueue(make_request(budget=budget))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(enqueue, 100)
        second = pool.submit(enqueue, 500)
        results = [first.result(timeout=10), second.result(timeout=10)]

    assert sum(result.created for result in results) == 1  # type: ignore[attr-defined]
    assert len({result.job_id for result in results}) == 1  # type: ignore[attr-defined]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(Collection)) == 1

    start_claim = Barrier(2)

    def claim(worker_id: str) -> object:
        start_claim.wait(timeout=5)
        return jobs.claim(worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_a = pool.submit(claim, "worker-a")
        claim_b = pool.submit(claim, "worker-b")
        leases = [claim_a.result(timeout=10), claim_b.result(timeout=10)]

    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1
    inspection = jobs.inspect(claimed[0].job_id)
    assert inspection.attempt_count == 1
    assert inspection.checkpoint.cursor is None
    assert inspection.checkpoint.next_page == 1
    assert inspection.checkpoint.revision == 0
    assert inspection.events[-1].event_type == "claimed"
    assert inspection.parameters_snapshot["parameters"]["budget"] in (100, 500)


def test_expired_lease_is_recovered_and_old_token_cannot_write(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())
    old_lease = jobs.claim("worker-old")
    assert old_lease is not None
    expire_lease(migrated_engine, created.job_id)

    recovered = jobs.claim("worker-new")
    assert recovered is not None
    assert recovered.job_id == old_lease.job_id
    assert recovered.possession_token != old_lease.possession_token
    assert recovered.attempt_number == 2

    with pytest.raises(LeaseLostError):
        jobs.advance_checkpoint(
            old_lease.job_id,
            old_lease.possession_token,
            expected_revision=0,
            cursor=["stale"],
            next_page=2,
        )

    inspection = jobs.inspect(created.job_id)
    assert inspection.checkpoint.next_page == 1
    assert inspection.checkpoint.revision == 0
    assert [attempt.outcome for attempt in inspection.attempts] == ["lease_expired", None]


def test_lease_expiring_during_result_transaction_rolls_back_without_reclaim(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=0.15)
    created = jobs.enqueue(make_request())
    lease = jobs.claim("worker-short-lease")
    assert lease is not None

    with pytest.raises(LeaseLostError):
        with jobs.owned_transaction(created.job_id, lease.possession_token) as session:
            session.execute(
                update(JobCheckpoint)
                .where(JobCheckpoint.job_id == created.job_id)
                .values(next_page=2, revision=1)
            )
            time.sleep(0.2)

    inspection = jobs.inspect(created.job_id)
    assert inspection.checkpoint.next_page == 1
    assert inspection.checkpoint.revision == 0
    assert inspection.attempt_count == 1


def test_heartbeat_uses_independent_session_while_handler_is_blocked(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=0.6)
    created = jobs.enqueue(make_request())
    handler_started = Event()
    release_handler = Event()

    def handler(_lease: JobLease, _context: JobExecutionContext) -> JobOutcome:
        handler_started.set()
        assert release_handler.wait(timeout=5)
        return JobOutcome(status="completed", coverage={"state": "handler_complete"})

    worker = LeasedWorker(
        jobs,
        worker_id="worker-heartbeat-test",
        handlers={"discovery": handler},
        heartbeat_interval=0.05,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run_once)
        assert handler_started.wait(timeout=5)
        first_heartbeat = jobs.inspect(created.job_id).heartbeat_at
        assert first_heartbeat is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            heartbeat_at = jobs.inspect(created.job_id).heartbeat_at
            if heartbeat_at is not None and heartbeat_at > first_heartbeat:
                break
            time.sleep(0.01)
        else:
            release_handler.set()
            pytest.fail("O heartbeat independente não foi confirmado durante o handler.")
        assert not release_handler.is_set()
        release_handler.set()
        assert running.result(timeout=5)

    inspection = jobs.inspect(created.job_id)
    assert inspection.status == "completed"
    assert inspection.attempts[0].outcome == "completed"


def test_cancel_preserves_checkpoint_and_prevents_later_progress(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())
    lease = jobs.claim("worker-cancel-test")
    assert lease is not None
    page_checkpoint = jobs.advance_checkpoint(
        created.job_id,
        lease.possession_token,
        expected_revision=0,
        cursor=["page-one"],
        next_page=2,
    )
    assert page_checkpoint.revision == 1

    assert jobs.request_cancel(created.job_id)
    with pytest.raises(JobCancellationRequestedError):
        jobs.advance_checkpoint(
            created.job_id,
            lease.possession_token,
            expected_revision=1,
            cursor=["page-two"],
            next_page=3,
        )

    final_status = jobs.finish(
        created.job_id,
        lease.possession_token,
        status="completed",
        coverage={"state": "partial"},
    )
    inspection = jobs.inspect(created.job_id)
    assert final_status == "cancelled"
    assert inspection.status == "cancelled"
    assert inspection.checkpoint.next_page == 2
    assert inspection.checkpoint.revision == 1
    assert inspection.events[-2].event_type == "cancel_requested"
    assert inspection.events[-1].event_type == "cancelled"


def test_cancel_waits_for_an_inflight_checkpoint_commit(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())
    lease = jobs.claim("worker-inflight-cancel")
    assert lease is not None
    transaction_locked_job = Event()
    release_transaction = Event()

    def commit_progress() -> None:
        with jobs.owned_transaction(created.job_id, lease.possession_token) as session:
            session.execute(
                update(JobCheckpoint)
                .where(JobCheckpoint.job_id == created.job_id)
                .values(cursor=["in-flight"], next_page=2, revision=1)
            )
            transaction_locked_job.set()
            assert release_transaction.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        commit = pool.submit(commit_progress)
        assert transaction_locked_job.wait(timeout=5)
        cancel = pool.submit(jobs.request_cancel, created.job_id)
        time.sleep(0.05)
        assert not cancel.done()
        release_transaction.set()
        commit.result(timeout=5)
        assert cancel.result(timeout=5)

    inspection = jobs.inspect(created.job_id)
    assert inspection.checkpoint.next_page == 2
    assert inspection.checkpoint.revision == 1
    assert inspection.cancel_requested
    with pytest.raises(JobCancellationRequestedError):
        jobs.advance_checkpoint(
            created.job_id,
            lease.possession_token,
            expected_revision=1,
            cursor=["after-cancel"],
            next_page=3,
        )


def test_heartbeat_database_failure_suspends_local_write_authorization(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=0.6)
    created = jobs.enqueue(make_request())

    def heartbeat_fails(_job_id: UUID, _token: UUID) -> object:
        raise RuntimeError("private connection details")

    monkeypatch.setattr(jobs, "heartbeat", heartbeat_fails)

    def handler(lease: JobLease, context: JobExecutionContext) -> JobOutcome:
        assert context.ownership_lost.wait(timeout=3)
        with pytest.raises(LeaseLostError):
            with context.owned_transaction(lease):
                pytest.fail("A handler with failed heartbeat must not write.")
        return JobOutcome(status="completed", coverage={"state": "must_not_commit"})

    worker = LeasedWorker(
        jobs,
        worker_id="worker-heartbeat-failure",
        handlers={"discovery": handler},
        heartbeat_interval=0.05,
    )
    assert worker.run_once()

    inspection = jobs.inspect(created.job_id)
    assert inspection.status == "running"
    assert inspection.checkpoint.revision == 0
    assert inspection.attempts[0].finished_at is None


def test_worker_restart_keeps_attempt_events_and_checkpoint(migrated_engine: Engine) -> None:
    first_process = make_service(migrated_engine)
    created = first_process.enqueue(make_request())
    first_lease = first_process.claim("worker-before-restart")
    assert first_lease is not None
    first_process.advance_checkpoint(
        created.job_id,
        first_lease.possession_token,
        expected_revision=0,
        cursor=["durable-cursor"],
        next_page=2,
    )
    expire_lease(migrated_engine, created.job_id)

    restarted_process = make_service(migrated_engine)
    second_lease = restarted_process.claim("worker-after-restart")
    assert second_lease is not None
    assert second_lease.attempt_number == 2
    inspection = restarted_process.inspect(created.job_id)

    assert inspection.checkpoint.cursor == ["durable-cursor"]
    assert inspection.checkpoint.next_page == 2
    assert inspection.checkpoint.revision == 1
    assert [attempt.outcome for attempt in inspection.attempts] == ["lease_expired", None]
    assert [event.event_number for event in inspection.events] == list(
        range(1, len(inspection.events) + 1)
    )
    assert [event.event_type for event in inspection.events] == [
        "enqueued",
        "claimed",
        "checkpoint_advanced",
        "lease_expired",
        "claimed",
    ]

    restarted_process.finish(
        created.job_id,
        second_lease.possession_token,
        status="completed",
        coverage={"state": "local_fixture"},
    )
    assert restarted_process.inspect(created.job_id).status == "completed"


def test_cancel_queued_is_immediate_and_does_not_claim(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())
    assert jobs.request_cancel(created.job_id)
    assert jobs.claim("worker-idle") is None
    inspection = jobs.inspect(created.job_id)
    assert inspection.status == "cancelled"
    assert inspection.attempt_count == 0
    assert inspection.events[-1].event_type == "cancelled"


def test_retry_wait_is_claimed_only_when_due_and_terminal_key_can_be_reused(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request(budget=100))
    first_lease = jobs.claim("worker-retry-first")
    assert first_lease is not None

    with Session(migrated_engine) as session:
        retry_at = session.scalar(select(func.clock_timestamp() + text("interval '1 minute'")))
    assert retry_at is not None
    assert (
        jobs.finish(
            created.job_id,
            first_lease.possession_token,
            status="retry_wait",
            reason="retry_later",
            retry_at=retry_at,
        )
        == "retry_wait"
    )

    equivalent = jobs.enqueue(make_request(budget=500))
    assert not equivalent.created
    assert equivalent.job_id == created.job_id
    assert jobs.claim("worker-too-early") is None

    with Session(migrated_engine) as session, session.begin():
        due_at = session.scalar(select(func.clock_timestamp() - text("interval '1 second'")))
        session.execute(update(Job).where(Job.id == created.job_id).values(available_at=due_at))
    second_lease = jobs.claim("worker-retry-second")
    assert second_lease is not None
    assert second_lease.attempt_number == 2
    jobs.finish(
        created.job_id,
        second_lease.possession_token,
        status="completed",
        coverage={"state": "partial_scope"},
        reason="budget",
    )

    next_job = jobs.enqueue(make_request(budget=500))
    assert next_job.created
    assert next_job.job_id != created.job_id
    assert next_job.operation_key == created.operation_key


def test_checkpoint_compare_and_swap_rejects_stale_revision(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())
    lease = jobs.claim("worker-cas")
    assert lease is not None
    jobs.advance_checkpoint(
        created.job_id,
        lease.possession_token,
        expected_revision=0,
        cursor=["one"],
        next_page=2,
    )

    with pytest.raises(CheckpointRevisionConflict):
        jobs.advance_checkpoint(
            created.job_id,
            lease.possession_token,
            expected_revision=0,
            cursor=["stale"],
            next_page=3,
        )
    assert jobs.inspect(created.job_id).checkpoint.next_page == 2


def test_job_parameter_snapshot_is_immutable_in_postgresql(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())

    with Session(migrated_engine) as session:
        with pytest.raises(DBAPIError, match="immutable"):
            with session.begin():
                session.execute(
                    update(Job)
                    .where(Job.id == created.job_id)
                    .values(parameters_snapshot={"changed": True})
                )

    inspection = jobs.inspect(created.job_id)
    assert inspection.parameters_snapshot["parameters"] == {"budget": 100, "page_size": 100}


def test_unexpected_handler_failure_is_persisted_without_raw_exception_text(
    migrated_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    jobs = make_service(migrated_engine)
    created = jobs.enqueue(make_request())

    def handler(_lease: JobLease, _context: JobExecutionContext) -> JobOutcome:
        raise RuntimeError("password=private-token")

    worker = LeasedWorker(
        jobs,
        worker_id="worker-sanitized-error",
        handlers={"discovery": handler},
        heartbeat_interval=0.05,
    )
    with caplog.at_level(logging.INFO):
        assert worker.run_once()

    inspection = jobs.inspect(created.job_id)
    assert inspection.status == "failed"
    assert inspection.attempts[0].error_code == "handler_error"
    assert inspection.attempts[0].error_summary == "Falha inesperada no handler."
    assert "private-token" not in caplog.text
