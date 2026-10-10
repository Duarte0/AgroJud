"""PostgreSQL acceptance evidence for persistent retries and recovery commands."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Collection,
    CollectionObservation,
    Job,
    RepresentationVersion,
    SourceRateLimit,
)
from agrojud.services.collection import CollectionJobHandler, build_collection_request
from agrojud.services.job_worker import JobExecutionContext, JobOutcome, LeasedWorker
from agrojud.services.jobs import (
    CursorInvalidConflict,
    EnqueueRequest,
    JobService,
    RequestDeferredError,
)
from agrojud.services.retry_policy import is_retryable_source_error, retry_deadline
from agrojud.sources.contracts import (
    Cursor,
    SourceError,
    SourceErrorCode,
    SourcePage,
    SourceQuery,
    parse_source_page,
)

CNJ = "00000000000000000001"


@pytest.fixture
def migrated_engine(scratch_database_url: URL) -> Iterator[Engine]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    yield engine
    engine.dispose()


def make_service(engine: Engine, *, lease_seconds: float = 120) -> JobService:
    return JobService(
        sessionmaker(engine, expire_on_commit=False),
        lease_duration=timedelta(seconds=lease_seconds),
    )


def query_for(day: int = 1) -> SourceQuery:
    start = date(2026, 1, day)
    return SourceQuery(filed_from=start, filed_to=start + timedelta(days=1))


def request_for(
    *,
    day: int = 1,
    source: str = "synthetic",
    mode: str = "demo",
    hit_budget: int = 10,
) -> EnqueueRequest:
    return build_collection_request(
        mode=mode,
        source=source,
        job_type="discovery",
        query=query_for(day),
        page_size=2,
        hit_budget=hit_budget,
    )


def enqueue(jobs: JobService, **kwargs: object) -> UUID:
    return jobs.enqueue(request_for(**kwargs)).job_id  # type: ignore[arg-type]


def source_hit(source_id: str = "one", second: int = 1) -> dict[str, object]:
    timestamp = f"2026-01-01T00:00:{second:02d}Z"
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": CNJ,
            "tribunal": "TJGO",
            "@timestamp": timestamp,
            "movimentos": [],
        },
        "sort": [timestamp, source_id],
    }


def source_page(hits: Sequence[dict[str, object]] = ()) -> SourcePage:
    return parse_source_page(
        {"hits": {"total": len(hits), "hits": list(hits)}},
        datetime.now(UTC),
    )


class SequenceAdapter:
    def __init__(self, responses: Sequence[SourcePage | SourceError]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def fetch_page(
        self,
        _query: SourceQuery,
        _cursor: Cursor | None,
        _page_size: int,
    ) -> SourcePage:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, SourceError):
            raise response
        return response

    def fetch_by_case_number(
        self,
        _process_number: str,
        cursor: Cursor | None = None,
        page_size: int = 100,
    ) -> SourcePage:
        return self.fetch_page(query_for(), cursor, page_size)


def handler_for(
    adapter: SequenceAdapter,
    *,
    clock: Callable[[], datetime] | None = None,
    random_value: Callable[[], float] = lambda: 0.0,
) -> CollectionJobHandler:
    return CollectionJobHandler(
        lambda _lease: adapter,
        clock=clock,
        random_value=random_value,
    )


def run_claimed_once(jobs: JobService, handler: CollectionJobHandler) -> JobOutcome:
    lease = jobs.claim("retry-test-worker")
    assert lease is not None
    context = JobExecutionContext(jobs, Event(), Event())
    outcome = handler(lease, context)
    jobs.finish(
        lease.job_id,
        lease.possession_token,
        status=outcome.status,
        coverage=outcome.coverage,
        reason=outcome.reason,
        retry_at=outcome.retry_at,
        error_code=outcome.error_code,
        error_summary=outcome.error_summary,
        source_cooldown_until=outcome.source_cooldown_until,
        cursor_invalid=outcome.cursor_invalid,
    )
    return outcome


def expire_lease(engine: Engine, job_id: UUID) -> None:
    with Session(engine) as session, session.begin():
        expired_at = session.scalar(select(func.clock_timestamp() - text("interval '1 second'")))
        session.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=expired_at))


def observation_fingerprint(engine: Engine, collection_id: UUID) -> tuple[int, tuple[str, ...]]:
    with Session(engine) as session:
        count = session.scalar(
            select(func.count())
            .select_from(CollectionObservation)
            .where(CollectionObservation.collection_id == collection_id)
        )
        hashes = session.scalars(
            select(RepresentationVersion.payload_sha256)
            .join(
                CollectionObservation,
                CollectionObservation.version_id == RepresentationVersion.id,
            )
            .where(CollectionObservation.collection_id == collection_id)
            .order_by(CollectionObservation.hit_ordinal)
        ).all()
    return int(count or 0), tuple(hashes)


def test_retry_deadline_uses_full_jitter_and_parses_retry_after() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert retry_deadline(now, attempt_number=1, random_value=lambda: 0.5) == now + timedelta(
        seconds=1
    )
    assert retry_deadline(
        now, attempt_number=2, retry_after="17", random_value=lambda: 0.1
    ) == now + timedelta(seconds=17)
    http_date = "Thu, 01 Jan 2026 00:00:23 GMT"
    assert retry_deadline(
        now, attempt_number=1, retry_after=http_date, random_value=lambda: 0
    ) == now + timedelta(seconds=23)
    assert retry_deadline(
        now, attempt_number=1, retry_after="invalid", random_value=lambda: 0.25
    ) == now + timedelta(seconds=0.5)
    assert retry_deadline(
        now,
        attempt_number=1,
        retry_after="Wed, 31 Dec 2025 23:59:00 GMT",
        random_value=lambda: 0.25,
    ) == now + timedelta(seconds=0.5)


def test_permanent_status_and_contract_errors_override_retryable_codes() -> None:
    assert not is_retryable_source_error(
        SourceError(SourceErrorCode.NETWORK, "Erro incoerente.", status_code=401)
    )
    assert not is_retryable_source_error(
        SourceError(SourceErrorCode.CONTRACT, "Erro de contrato.", status_code=503)
    )


def test_429_retry_wait_survives_service_restart_and_then_succeeds(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs)
    started_at = jobs.current_database_time()
    adapter = SequenceAdapter(
        [
            SourceError(
                SourceErrorCode.RATE_LIMIT,
                "A fonte limitou a frequência.",
                status_code=429,
                retry_after="17",
            ),
            source_page(),
        ]
    )
    handler = handler_for(adapter, clock=jobs.current_database_time)

    first = run_claimed_once(jobs, handler)
    assert first.status == "retry_wait"
    saved = jobs.inspect(job_id)
    assert saved.status == "retry_wait"
    assert saved.page_attempt_count == 1
    assert saved.coverage is not None
    assert saved.coverage["http_attempts"] == 1
    assert saved.next_attempt_at >= started_at + timedelta(seconds=17)
    assert saved.next_attempt_at.tzinfo is not None
    with Session(migrated_engine) as session:
        source_limit = session.get(SourceRateLimit, "synthetic")
        assert source_limit is not None
        assert source_limit.cooldown_until is not None

    # A replacement service reads the database schedule and consumes the same page retry.
    restarted = make_service(migrated_engine)
    assert restarted.claim("replacement-worker") is None
    # Move the persisted deadline forward without waiting 17 seconds in the test.
    with Session(migrated_engine) as session, session.begin():
        due_at = session.scalar(select(func.clock_timestamp() - text("interval '1 second'")))
        session.execute(update(Job).where(Job.id == job_id).values(next_attempt_at=due_at))
        session.execute(
            update(SourceRateLimit)
            .where(SourceRateLimit.source == "synthetic")
            .values(cooldown_until=due_at)
        )
    second = run_claimed_once(restarted, handler)
    assert second.status == "completed"
    completed = restarted.inspect(job_id)
    assert completed.checkpoint.revision == 1
    assert completed.coverage is not None
    assert completed.coverage["http_attempts"] == 2
    assert completed.attempts[0].outcome == "retry_wait"
    assert completed.attempts[1].outcome == "completed"


def test_http_retry_budget_exhausts_after_five_5xx_and_401_is_not_retried(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    unavailable_id = enqueue(jobs, day=2)
    unavailable = SequenceAdapter(
        [
            SourceError(
                SourceErrorCode.SOURCE_UNAVAILABLE,
                "Serviço temporariamente indisponível.",
                status_code=503,
            )
            for _ in range(5)
        ]
    )
    handler = handler_for(unavailable, clock=lambda: datetime.now(UTC))

    outcomes = [run_claimed_once(jobs, handler) for _ in range(5)]
    assert [item.status for item in outcomes] == [
        "retry_wait",
        "retry_wait",
        "retry_wait",
        "retry_wait",
        "failed",
    ]
    exhausted = jobs.inspect(unavailable_id)
    assert unavailable.calls == 5
    assert exhausted.status == "failed"
    assert exhausted.reason == "retry_exhausted"
    assert exhausted.page_attempt_count == 5
    assert exhausted.coverage is not None
    assert exhausted.coverage["http_attempts"] == 5

    permanent_errors = (
        (SourceErrorCode.AUTHENTICATION, 401),
        (SourceErrorCode.AUTHORIZATION, 403),
        (SourceErrorCode.VALIDATION, 400),
        (SourceErrorCode.CONTRACT, None),
    )
    for day, (code, status_code) in enumerate(permanent_errors, start=3):
        permanent_id = enqueue(jobs, day=day)
        permanent = SequenceAdapter(
            [
                SourceError(
                    code,
                    "Falha permanente ou de contrato.",
                    status_code=status_code,
                )
            ]
        )
        outcome = run_claimed_once(jobs, handler_for(permanent))
        assert outcome.status == "failed"
        assert permanent.calls == 1
        assert jobs.inspect(permanent_id).reason == "source_error"


def test_resume_preserves_checkpoint_history_and_starts_new_cycle(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=4)
    adapter = SequenceAdapter(
        [
            SourceError(
                SourceErrorCode.AUTHENTICATION,
                "A credencial foi recusada.",
                status_code=401,
            ),
            source_page([source_hit()]),
            source_page(),
        ]
    )
    assert run_claimed_once(jobs, handler_for(adapter)).status == "failed"
    failed = jobs.inspect(job_id)
    resumed_id = jobs.resume(job_id)
    assert resumed_id == job_id
    resumed = jobs.inspect(job_id)
    assert resumed.status == "queued"
    assert resumed.retry_cycle == failed.retry_cycle + 1
    assert resumed.checkpoint == failed.checkpoint
    assert resumed.attempt_count == failed.attempt_count
    assert resumed.page_attempt_count == 0
    assert jobs.resume(job_id) == job_id
    assert jobs.inspect(job_id).retry_cycle == resumed.retry_cycle

    assert run_claimed_once(jobs, handler_for(adapter)).status == "completed"
    complete = jobs.inspect(job_id)
    assert complete.checkpoint.revision == 2
    assert complete.coverage is not None
    assert complete.coverage["http_attempts"] == 3
    assert [event.event_type for event in complete.events].count("resumed") == 1


def test_cancelled_job_can_resume_without_losing_checkpoint(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=5)
    assert jobs.request_cancel(job_id)
    cancelled = jobs.inspect(job_id)
    assert cancelled.status == "cancelled"

    assert jobs.resume(job_id) == job_id
    resumed = jobs.inspect(job_id)
    assert resumed.status == "queued"
    assert resumed.retry_cycle == 2
    assert resumed.checkpoint == cancelled.checkpoint
    assert resumed.events[-1].event_type == "resumed"


def test_continue_limit_extends_budget_without_changing_cursor_or_revision(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=6, hit_budget=1)
    assert (
        run_claimed_once(
            jobs,
            handler_for(SequenceAdapter([source_page([source_hit()])])),
        ).status
        == "partial"
    )
    limited = jobs.inspect(job_id)
    assert limited.reason == "limit"
    assert limited.coverage is not None
    assert limited.coverage["budget_limit"] == 1

    assert jobs.continue_job(job_id, additional_budget=2) == job_id
    continued = jobs.inspect(job_id)
    assert continued.status == "queued"
    assert continued.checkpoint == limited.checkpoint
    assert continued.coverage is not None
    assert continued.coverage["budget_limit"] == 3
    assert continued.coverage["hits_confirmed"] == 1
    assert continued.coverage["continuations"] == 1
    assert continued.events[-1].event_type == "continued"


def test_invalid_cursor_requires_explicit_new_scan_with_predecessor(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    old_id = enqueue(jobs, day=7)
    adapter = SequenceAdapter(
        [
            source_page([source_hit()]),
            SourceError(
                SourceErrorCode.CURSOR_INVALID,
                "O cursor da consulta foi rejeitado.",
                status_code=400,
            ),
        ]
    )
    assert run_claimed_once(jobs, handler_for(adapter)).status == "failed"
    old = jobs.inspect(old_id)
    assert old.cursor_invalid
    assert old.reason == "cursor_invalid"
    with pytest.raises(CursorInvalidConflict):
        jobs.resume(old_id)

    restarted = jobs.restart_scan(old_id)
    assert restarted.created
    assert restarted.job_id != old_id
    new = jobs.inspect(restarted.job_id)
    assert new.predecessor_job_id == old_id
    assert new.collection_id != old.collection_id
    with Session(migrated_engine) as session:
        new_collection = session.get(Collection, new.collection_id)
        assert new_collection is not None
        assert new_collection.previous_collection_id == old.collection_id
    assert new.checkpoint.revision == 0
    assert new.checkpoint.cursor is None

    duplicate = jobs.restart_scan(old_id)
    assert not duplicate.created
    assert duplicate.job_id == restarted.job_id


def test_persisted_source_cooldown_blocks_an_equivalent_source_switch(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    first_id = enqueue(jobs, day=8)
    lease = jobs.claim("cooldown-worker")
    assert lease is not None
    due = jobs.current_database_time() + timedelta(minutes=2)
    jobs.finish(
        first_id,
        lease.possession_token,
        status="retry_wait",
        retry_at=due,
        reason="source_retry",
        source_cooldown_until=due,
        error_code="RATE_LIMIT",
        error_summary="Limite temporário da fonte.",
    )
    second_id = enqueue(jobs, day=9)
    second = jobs.inspect(second_id)
    assert second.next_attempt_at >= due
    replacement_service = make_service(migrated_engine)
    assert replacement_service.claim("replacement-worker") is None

    with Session(migrated_engine) as session:
        source_limit = session.get(SourceRateLimit, "synthetic")
        assert source_limit is not None
        assert source_limit.cooldown_until is not None
        assert source_limit.cooldown_until >= due


def test_two_consumers_get_spaced_slots_from_one_persistent_source_limiter(
    migrated_engine: Engine,
) -> None:
    jobs = make_service(migrated_engine)
    first_id = enqueue(jobs, day=10, source="datajud", mode="real")
    second_id = enqueue(jobs, day=11, source="datajud", mode="real")
    first = jobs.claim("consumer-one")
    second = jobs.claim("consumer-two")
    assert first is not None and first.job_id == first_id
    assert second is not None and second.job_id == second_id
    barrier = Barrier(2)

    def reserve(lease: object) -> tuple[str, object]:
        from agrojud.services.jobs import JobLease

        assert isinstance(lease, JobLease)
        barrier.wait(timeout=5)
        try:
            result = jobs.record_http_attempt(
                lease.job_id,
                lease.possession_token,
                expected_revision=0,
                budget_limit=10,
            )
            return "started", result
        except RequestDeferredError as error:
            return "deferred", error.retry_at

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (first, second)))
    assert sorted(kind for kind, _ in results) == ["deferred", "started"]
    deferred = next(value for kind, value in results if kind == "deferred")
    assert isinstance(deferred, datetime)

    with Session(migrated_engine) as session, session.begin():
        session.execute(
            update(SourceRateLimit)
            .where(SourceRateLimit.source == "datajud")
            .values(next_request_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    deferred_lease = second if results[1][0] == "deferred" else first
    assert (
        jobs.record_http_attempt(
            deferred_lease.job_id,
            deferred_lease.possession_token,
            expected_revision=0,
            budget_limit=10,
        )
        == 1
    )


def test_persistence_retries_same_page_and_reconciles_unknown_commit(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agrojud.services.job_worker import JobExecutionContext

    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=12)
    adapter = SequenceAdapter([source_page([source_hit()]), source_page()])
    original_commit = JobExecutionContext.commit_page
    calls = 0

    def lose_first_ack(
        context: JobExecutionContext,
        lease: object,
        **kwargs: object,
    ) -> object:
        nonlocal calls
        calls += 1
        result = original_commit(context, lease, **kwargs)  # type: ignore[arg-type]
        if calls == 1:
            raise OperationalError(
                "COMMIT",
                {},
                RuntimeError("acknowledgment lost"),
                connection_invalidated=True,
            )
        return result

    monkeypatch.setattr(JobExecutionContext, "commit_page", lose_first_ack)
    outcome = run_claimed_once(jobs, handler_for(adapter))
    assert outcome.status == "completed"
    assert calls == 2
    inspection = jobs.inspect(job_id)
    assert inspection.checkpoint.revision == 2
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 1
    assert inspection.coverage["http_attempts"] == 2
    assert observation_fingerprint(migrated_engine, inspection.collection_id)[0] == 1


def test_transient_commit_failure_retries_persistently_without_refetch(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agrojud.services.job_worker import JobExecutionContext

    class DeadlockError(RuntimeError):
        sqlstate = "40P01"

    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=13)
    adapter = SequenceAdapter([source_page([source_hit()]), source_page()])
    original_commit = JobExecutionContext.commit_page
    calls = 0

    def fail_once(
        context: JobExecutionContext,
        lease: object,
        **kwargs: object,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("INSERT", {}, DeadlockError("deadlock"))
        return original_commit(context, lease, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(JobExecutionContext, "commit_page", fail_once)
    outcome = run_claimed_once(jobs, handler_for(adapter))
    assert outcome.status == "completed"
    assert calls == 3
    assert adapter.calls == 2
    inspection = jobs.inspect(job_id)
    assert inspection.checkpoint.revision == 2
    assert observation_fingerprint(migrated_engine, inspection.collection_id)[0] == 1
    persistence_events = [
        event.details
        for event in inspection.events
        if event.event_type == "page_persistence_attempted"
    ]
    assert [event["persistence_attempt"] for event in persistence_events] == [1, 2, 1]


def test_persistence_retry_exhaustion_does_not_advance_page(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agrojud.services.job_worker import JobExecutionContext

    class DeadlockError(RuntimeError):
        sqlstate = "40P01"

    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=14)
    adapter = SequenceAdapter([source_page([source_hit()])])
    calls = 0

    def always_deadlock(
        _context: JobExecutionContext,
        _lease: object,
        **_kwargs: object,
    ) -> object:
        nonlocal calls
        calls += 1
        raise OperationalError("INSERT", {}, DeadlockError("deadlock"))

    monkeypatch.setattr(JobExecutionContext, "commit_page", always_deadlock)
    outcome = run_claimed_once(jobs, handler_for(adapter))
    assert outcome.status == "failed"
    assert calls == 5
    inspection = jobs.inspect(job_id)
    assert inspection.reason == "persistence_retry_exhausted"
    assert inspection.checkpoint.revision == 0
    assert inspection.persistence_attempt_count == 5
    assert observation_fingerprint(migrated_engine, inspection.collection_id)[0] == 0


@pytest.mark.parametrize(("phase", "exit_code"), [("before", 73), ("after", 74)])
def test_real_subprocess_crash_before_and_after_commit_matches_continuous_run(
    migrated_engine: Engine,
    scratch_database_url: URL,
    phase: str,
    exit_code: int,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=5)
    recovered_id = enqueue(jobs, day=15 if phase == "before" else 16)
    lease = jobs.claim("subprocess-preparation")
    assert lease is not None and lease.job_id == recovered_id

    # Return the preparation lease before the child process claims the job.
    jobs.finish(
        lease.job_id,
        lease.possession_token,
        status="retry_wait",
        retry_at=jobs.current_database_time() - timedelta(seconds=1),
        reason="test_handoff",
    )

    child_code = r"""
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy.orm import sessionmaker
from agrojud.db.engine import make_engine
from agrojud.services.jobs import JobService
from agrojud.sources.contracts import parse_source_page

phase = sys.argv[1]
job_id = UUID(sys.argv[2])
engine = make_engine(os.environ["CHILD_DATABASE_URL"])
jobs = JobService(sessionmaker(engine, expire_on_commit=False))
lease = jobs.claim("crash-subprocess")
if lease is None or lease.job_id != job_id:
    os._exit(72)
jobs.record_http_attempt(
    lease.job_id, lease.possession_token, expected_revision=0, budget_limit=10
)
jobs.record_persistence_attempt(
    lease.job_id, lease.possession_token, expected_revision=0
)
page = parse_source_page(
    {"hits": {"total": 1, "hits": [{
        "_id": "subprocess-hit",
        "_source": {
            "id": "subprocess-hit",
            "numeroProcesso": "00000000000000000001",
            "tribunal": "TJGO",
            "@timestamp": "2026-01-01T00:00:01Z",
            "movimentos": [],
        },
        "sort": ["2026-01-01T00:00:01Z", "subprocess-hit"],
    }]}},
    datetime.now(UTC),
)
if phase == "before":
    original = jobs.owned_transaction
    @contextmanager
    def crash_before_commit(job_id, possession_token, **kwargs):
        with original(job_id, possession_token, **kwargs) as session:
            yield session
            os._exit(73)
    jobs.owned_transaction = crash_before_commit
jobs.commit_page(
    lease.job_id, lease.possession_token, expected_revision=0,
    budget_limit=10, source="synthetic", page=page
)
os._exit(74)
"""
    child_url = scratch_database_url.render_as_string(hide_password=False)
    environment = {**os.environ, "CHILD_DATABASE_URL": child_url}
    # Use the checked-out source tree so the child runs the exact code under test.
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join([source_root, environment.get("PYTHONPATH", "")])
    child = subprocess.run(
        [sys.executable, "-c", child_code, phase, str(recovered_id)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert child.returncode == exit_code, child.stderr

    before_recovery = jobs.inspect(recovered_id)
    assert before_recovery.coverage is not None
    assert before_recovery.coverage["http_attempts"] == 1
    if phase == "before":
        assert before_recovery.checkpoint.revision == 0
        assert before_recovery.persistence_attempt_count == 1
        assert observation_fingerprint(migrated_engine, before_recovery.collection_id)[0] == 0
    else:
        assert before_recovery.checkpoint.revision == 1
        assert before_recovery.persistence_attempt_count == 0
        assert observation_fingerprint(migrated_engine, before_recovery.collection_id)[0] == 1

    expire_lease(migrated_engine, recovered_id)
    recovered_lease = jobs.claim("process-restarted")
    assert recovered_lease is not None
    pages = (
        [source_page([source_hit("subprocess-hit")]), source_page()]
        if phase == "before"
        else [source_page()]
    )
    recovered_outcome = handler_for(SequenceAdapter(pages))(
        recovered_lease,
        JobExecutionContext(jobs, Event(), Event()),
    )
    jobs.finish(
        recovered_id,
        recovered_lease.possession_token,
        status=recovered_outcome.status,
        coverage=recovered_outcome.coverage,
        reason=recovered_outcome.reason,
    )
    recovered = jobs.inspect(recovered_id)

    baseline_id = enqueue(jobs, day=17 if phase == "before" else 18)
    baseline = make_service(migrated_engine)
    baseline_outcome = run_claimed_once(
        baseline,
        handler_for(SequenceAdapter([source_page([source_hit("subprocess-hit")]), source_page()])),
    )
    assert baseline_outcome.status == "completed"
    baseline_inspection = baseline.inspect(baseline_id)
    assert recovered.status == "completed"
    assert recovered.checkpoint.revision == baseline_inspection.checkpoint.revision == 2
    assert recovered.checkpoint.cursor == baseline_inspection.checkpoint.cursor
    assert recovered.recovery_count == 0
    assert observation_fingerprint(
        migrated_engine, recovered.collection_id
    ) == observation_fingerprint(migrated_engine, baseline_inspection.collection_id)
    assert recovered.coverage is not None and baseline_inspection.coverage is not None
    assert recovered.coverage["http_attempts"] == baseline_inspection.coverage["http_attempts"] + (
        1 if phase == "before" else 0
    )
    for key in ("pages_confirmed", "hits_confirmed", "valid_hits", "rejected_hits"):
        assert recovered.coverage[key] == baseline_inspection.coverage[key]


def test_database_unavailability_leaves_lease_to_expire_without_failed_state(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=5)
    job_id = enqueue(jobs, day=19)
    original_inspect = jobs.inspect
    inspect_calls = 0

    def fail_commit_reconciliation(job: UUID) -> object:
        nonlocal inspect_calls
        inspect_calls += 1
        if inspect_calls >= 3:
            raise OperationalError(
                "SELECT", {}, RuntimeError("connection lost"), connection_invalidated=True
            )
        return original_inspect(job)

    monkeypatch.setattr(jobs, "inspect", fail_commit_reconciliation)
    adapter = SequenceAdapter([source_page([source_hit()])])
    worker = LeasedWorker(
        jobs,
        worker_id="db-down-worker",
        handlers={"discovery": handler_for(adapter)},
        heartbeat_interval=1,
    )
    assert worker.run_once()
    monkeypatch.setattr(jobs, "inspect", original_inspect)
    inspection = jobs.inspect(job_id)
    assert inspection.status == "running"
    assert inspection.attempts[-1].finished_at is None
    assert inspection.reason is None


def test_database_unavailability_on_initial_read_leaves_lease_unconfirmed(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = make_service(migrated_engine, lease_seconds=5)
    job_id = enqueue(jobs, day=21)
    original_inspect = jobs.inspect

    def fail_inspect(_job_id: UUID) -> object:
        raise OperationalError(
            "SELECT", {}, RuntimeError("connection lost"), connection_invalidated=True
        )

    monkeypatch.setattr(jobs, "inspect", fail_inspect)
    adapter = SequenceAdapter([source_page([source_hit()])])
    worker = LeasedWorker(
        jobs,
        worker_id="db-down-before-read-worker",
        handlers={"discovery": handler_for(adapter)},
        heartbeat_interval=1,
    )
    assert worker.run_once()
    monkeypatch.setattr(jobs, "inspect", original_inspect)
    inspection = jobs.inspect(job_id)
    assert inspection.status == "running"
    assert inspection.attempts[-1].finished_at is None
    assert inspection.reason is None
    assert adapter.calls == 0


def test_recovery_exhaustion_is_bounded_and_persisted(migrated_engine: Engine) -> None:
    jobs = make_service(migrated_engine)
    job_id = enqueue(jobs, day=20)
    lease = jobs.claim("recovery-start")
    assert lease is not None

    for recovery_number in range(1, 6):
        expire_lease(migrated_engine, job_id)
        lease = jobs.claim(f"recovery-{recovery_number}")
        assert lease is not None
        assert jobs.inspect(job_id).recovery_count == recovery_number
    expire_lease(migrated_engine, job_id)

    assert jobs.claim("recovery-exhausted") is None
    exhausted = jobs.inspect(job_id)
    assert exhausted.status == "failed"
    assert exhausted.reason == "recovery_exhausted"
    assert exhausted.recovery_count == 6
    assert exhausted.events[-1].event_type == "recovery_exhausted"
