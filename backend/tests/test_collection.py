"""PostgreSQL acceptance evidence for paginated collection and checkpoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    CollectionObservation,
    Job,
    QuarantineRejection,
    Representation,
)
from agrojud.services.collection import (
    CollectionJobHandler,
    build_collection_request,
    plan_limit_continuation,
)
from agrojud.services.job_worker import JobExecutionContext, JobOutcome, LeasedWorker
from agrojud.services.jobs import JobLease, JobService
from agrojud.sources.contracts import (
    Cursor,
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    SourceQuery,
    build_query_by_case_number,
    parse_source_page,
)
from agrojud.sources.synthetic import (
    SyntheticPageFixture,
    SyntheticQueryFixture,
    SyntheticSourceAdapter,
)

CNJ = "00000000000000000001"


@pytest.fixture
def migrated_engine(scratch_database_url: Any) -> Engine:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    yield engine
    engine.dispose()


def discovery_query() -> SourceQuery:
    return SourceQuery(filed_from=date(2026, 1, 1), filed_to=date(2026, 2, 1))


def source_hit(
    source_id: str,
    second: int,
    *,
    process_number: str = CNJ,
) -> dict[str, Any]:
    timestamp = f"2026-01-01T00:00:{second:02d}Z"
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": process_number,
            "tribunal": "TJGO",
            "@timestamp": timestamp,
            "movimentos": [],
        },
        "sort": [timestamp],
    }


def source_page(hits: Sequence[Mapping[str, Any]]) -> SourcePage:
    return parse_source_page(
        {"hits": {"total": None, "hits": list(hits)}},
        datetime.now(UTC),
    )


def make_jobs(engine: Engine, *, lease_seconds: float = 120) -> JobService:
    return JobService(
        sessionmaker(engine, expire_on_commit=False),
        lease_duration=timedelta(seconds=lease_seconds),
    )


def expire_lease(engine: Engine, job_id: UUID) -> None:
    with Session(engine) as session, session.begin():
        expired_at = session.scalar(select(func.clock_timestamp() - text("interval '1 second'")))
        session.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=expired_at))


def make_handler(adapter: Any) -> CollectionJobHandler:
    return CollectionJobHandler(lambda _lease: adapter)


def enqueue(
    jobs: JobService,
    query: SourceQuery,
    *,
    job_type: str = "discovery",
    mode: str = "demo",
    source: str = "synthetic",
    page_size: int = 2,
    hit_budget: int = 2_000,
) -> UUID:
    result = jobs.enqueue(
        build_collection_request(
            mode=mode,
            source=source,
            job_type=job_type,
            query=query,
            page_size=page_size,
            hit_budget=hit_budget,
        )
    )
    return result.job_id


def run_worker(
    jobs: JobService,
    job_type: str,
    handler: Callable[[JobLease, JobExecutionContext], JobOutcome],
) -> None:
    worker = LeasedWorker(
        jobs,
        worker_id="collection-test-worker",
        handlers={job_type: handler},  # type: ignore[dict-item]
        heartbeat_interval=0.1,
    )
    assert worker.run_once()


def fixture_adapter(
    query: SourceQuery,
    pages: Sequence[Sequence[Mapping[str, Any]]],
) -> SyntheticSourceAdapter:
    return SyntheticSourceAdapter(
        {
            query: SyntheticQueryFixture(
                pages=tuple(SyntheticPageFixture(tuple(page)) for page in pages)
            )
        }
    )


class RecordingAdapter:
    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped
        self.sizes: list[int] = []
        self.cursors: list[Cursor | None] = []

    def fetch_page(
        self,
        query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        self.sizes.append(page_size)
        self.cursors.append(cursor)
        return self.wrapped.fetch_page(query, cursor, page_size)

    def fetch_by_case_number(
        self,
        process_number: str,
        cursor: Cursor | None = None,
        page_size: int = 100,
    ) -> SourcePage:
        self.sizes.append(page_size)
        self.cursors.append(cursor)
        return self.wrapped.fetch_by_case_number(process_number, cursor, page_size)


def test_empty_single_and_short_pages_continue_until_empty_is_confirmed(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()
    scenarios = (
        ((), 0, 1),
        (((source_hit("one", 1),),), 1, 2),
        (
            (
                (source_hit("one", 1), source_hit("two", 2)),
                (source_hit("three", 3),),
            ),
            3,
            3,
        ),
    )

    for pages, expected_hits, expected_requests in scenarios:
        jobs = make_jobs(migrated_engine)
        job_id = enqueue(jobs, query)
        adapter = RecordingAdapter(fixture_adapter(query, pages))
        run_worker(jobs, "discovery", make_handler(adapter))

        inspection = jobs.inspect(job_id)
        assert inspection.status == "completed"
        assert inspection.coverage is not None
        assert inspection.coverage["hits_confirmed"] == expected_hits
        assert inspection.coverage["pages_confirmed"] == expected_requests
        assert inspection.coverage["http_attempts"] == expected_requests
        assert inspection.coverage["query_status"] == "exhausted"
        assert len(adapter.sizes) == expected_requests
        assert inspection.checkpoint.revision == expected_requests
        if pages:
            assert inspection.coverage["valid_hits"] == expected_hits
            assert inspection.coverage["rejected_hits"] == 0
        else:
            assert inspection.coverage["has_persisted_data"] is False


def test_budget_bounds_each_request_and_continuation_keeps_progress(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()
    adapter = RecordingAdapter(
        fixture_adapter(
            query,
            (
                (
                    source_hit("one", 1),
                    source_hit("two", 2),
                    source_hit("three", 3),
                    source_hit("four", 4),
                ),
            ),
        )
    )
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query, page_size=2, hit_budget=3)
    run_worker(jobs, "discovery", make_handler(adapter))

    inspection = jobs.inspect(job_id)
    assert inspection.status == "partial"
    assert inspection.reason == "limit"
    assert inspection.checkpoint.cursor == ["2026-01-01T00:00:03Z"]
    assert adapter.sizes == [2, 1]
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 3
    assert inspection.coverage["valid_hits"] + inspection.coverage["rejected_hits"] == 3
    assert inspection.coverage["new"] == 3
    assert inspection.coverage["http_attempts"] == 2
    assert inspection.coverage["has_persisted_data"] is True

    continued = plan_limit_continuation(
        status=inspection.status,
        reason=inspection.reason,
        cursor=inspection.checkpoint.cursor,
        checkpoint_revision=inspection.checkpoint.revision,
        coverage=inspection.coverage,
        additional_budget=2_000,
    )
    assert continued.cursor == tuple(inspection.checkpoint.cursor)
    assert continued.checkpoint_revision == inspection.checkpoint.revision
    assert continued.coverage["budget_limit"] == 2_003
    assert continued.coverage["hits_confirmed"] == 3
    assert continued.coverage["checkpoint_revision"] == inspection.checkpoint.revision
    assert continued.coverage["http_attempts"] == 2
    assert continued.coverage["query_status"] == "running"
    with pytest.raises(SourceError):
        plan_limit_continuation(
            status="partial",
            reason="rejections",
            cursor=inspection.checkpoint.cursor,
            checkpoint_revision=inspection.checkpoint.revision,
            coverage=inspection.coverage,
            additional_budget=1,
        )
    with pytest.raises(SourceError):
        plan_limit_continuation(
            status="partial",
            reason="limit",
            cursor=None,
            checkpoint_revision=inspection.checkpoint.revision,
            coverage=inspection.coverage,
            additional_budget=1,
        )
    with pytest.raises(SourceError):
        plan_limit_continuation(
            status="partial",
            reason="limit",
            cursor=inspection.checkpoint.cursor,
            checkpoint_revision=inspection.checkpoint.revision,
            coverage=inspection.coverage,
            additional_budget=2_001,
        )


def test_page_commit_and_exhaustion_replay_keep_effects_and_counters(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()
    adapter = RecordingAdapter(fixture_adapter(query, ((source_hit("one", 1),),)))
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    lease = jobs.claim("first-worker")
    assert lease is not None
    context = JobExecutionContext(jobs, Event(), Event())

    outcome = make_handler(adapter)(lease, context)
    assert outcome.status == "completed"
    assert outcome.coverage is not None
    # The process is lost before finish: the exhaustion checkpoint is durable.
    expire_lease(migrated_engine, job_id)
    recovered = jobs.claim("recovery-worker")
    assert recovered is not None
    recovered_outcome = make_handler(adapter)(recovered, context)
    assert recovered_outcome.status == "completed"
    assert len(adapter.sizes) == 2
    assert recovered_outcome.coverage == outcome.coverage
    jobs.finish(
        recovered.job_id,
        recovered.possession_token,
        status="completed",
        coverage=recovered_outcome.coverage,
    )

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(CollectionObservation)) == 1
    inspection = jobs.inspect(job_id)
    assert inspection.checkpoint.revision == 2
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 1
    assert inspection.coverage["pages_confirmed"] == 2
    assert inspection.coverage["http_attempts"] == 2

    continuous_id = enqueue(jobs, query)
    continuous_adapter = RecordingAdapter(
        fixture_adapter(query, ((source_hit("continuous-one", 1),),))
    )
    run_worker(jobs, "discovery", make_handler(continuous_adapter))
    continuous = jobs.inspect(continuous_id)
    assert continuous.status == "completed"
    assert continuous.coverage == inspection.coverage


def test_quarantine_write_failure_rolls_back_data_and_checkpoint_then_replays(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agrojud.db.movement_repositories import QuarantineRepository

    query = discovery_query()
    invalid_hit = {"_id": "invalid", "sort": ["2026-01-01T00:00:01Z"]}
    adapter = RecordingAdapter(fixture_adapter(query, ((invalid_hit,),)))
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    lease = jobs.claim("rollback-worker")
    assert lease is not None
    context = JobExecutionContext(jobs, Event(), Event())
    original = QuarantineRepository.create_or_get
    page_keys: list[str] = []

    def fail_once(repository: QuarantineRepository, **kwargs: Any) -> Any:
        page_keys.append(str(kwargs["page_key"]))
        if len(page_keys) == 1:
            raise RuntimeError("simulated quarantine write failure")
        return original(repository, **kwargs)

    monkeypatch.setattr(QuarantineRepository, "create_or_get", fail_once)
    failed = make_handler(adapter)(lease, context)
    assert failed.status == "failed"
    assert failed.reason == "persistence_error"
    inspection = jobs.inspect(job_id)
    assert inspection.checkpoint.revision == 0
    assert inspection.checkpoint.next_page == 1
    assert inspection.coverage is not None
    assert inspection.coverage["pages_confirmed"] == 0
    assert inspection.coverage["hits_confirmed"] == 0
    assert inspection.coverage["http_attempts"] == 1
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(QuarantineRejection)) == 0

    # Same lease/revision uses the same idempotency key after the rollback.
    replay = make_handler(adapter)(lease, context)
    assert replay.status == "partial"
    assert replay.reason == "rejections"
    assert page_keys[0] != ""
    assert page_keys[0] == page_keys[1]
    inspection = jobs.inspect(job_id)
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 1
    assert inspection.coverage["valid_hits"] == 0
    assert inspection.coverage["rejected_hits"] == 1
    assert inspection.coverage["quarantine_records"] == 1
    assert inspection.checkpoint.revision == 2
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(QuarantineRejection)) == 1


def test_cursor_repetition_fails_without_committing_the_invalid_page(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()

    class RepeatingCursorAdapter:
        def __init__(self) -> None:
            self.pages = [
                source_page((source_hit("one", 1),)),
                source_page((source_hit("two", 1),)),
            ]

        def fetch_page(
            self, _query: SourceQuery, _cursor: Cursor | None, _page_size: int
        ) -> SourcePage:
            if self.pages:
                return self.pages.pop(0)
            return source_page(())

        def fetch_by_case_number(
            self, _process_number: str, cursor=None, page_size=100
        ) -> SourcePage:
            return self.fetch_page(query, cursor, page_size)

    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    run_worker(jobs, "discovery", make_handler(RepeatingCursorAdapter()))

    inspection = jobs.inspect(job_id)
    assert inspection.status == "failed"
    assert inspection.attempts[-1].error_code == SourceErrorCode.CONTRACT.value
    assert inspection.checkpoint.revision == 1
    assert inspection.checkpoint.cursor == ["2026-01-01T00:00:01Z"]
    assert inspection.coverage is not None
    assert inspection.coverage["query_status"] == "failed"
    assert inspection.coverage["has_persisted_data"] is True
    assert inspection.coverage["coverage"] == "partial"
    assert inspection.coverage["hits_confirmed"] == 1


def test_sort_missing_from_a_hit_fails_before_page_commit(migrated_engine: Engine) -> None:
    query = discovery_query()

    class MissingSortAdapter:
        def fetch_page(
            self, _query: SourceQuery, _cursor: Cursor | None, _page_size: int
        ) -> SourcePage:
            hit = SourceHit(
                source_id="one",
                source={
                    "id": "one",
                    "numeroProcesso": CNJ,
                    "tribunal": "TJGO",
                    "movimentos": [],
                },
                sort_values=(),
                raw={},
            )
            return SourcePage(
                hits=(hit,),
                total_value=None,
                total_relation=None,
                cursor_final=(),
                responded_at=datetime.now(UTC),
            )

        def fetch_by_case_number(
            self, _process_number: str, cursor=None, page_size=100
        ) -> SourcePage:
            return self.fetch_page(query, cursor, page_size)

    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    run_worker(jobs, "discovery", make_handler(MissingSortAdapter()))

    inspection = jobs.inspect(job_id)
    assert inspection.status == "failed"
    assert inspection.attempts[-1].error_code == SourceErrorCode.CONTRACT.value
    assert inspection.checkpoint.revision == 0
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 0
    assert inspection.coverage["pages_confirmed"] == 0
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(CollectionObservation)) == 0


def test_loss_of_possession_during_http_discards_the_returned_page(
    migrated_engine: Engine,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    query = discovery_query()
    jobs = make_jobs(migrated_engine, lease_seconds=5)
    job_id = enqueue(jobs, query)
    http_started = Event()
    release_http = Event()
    captured_contexts: list[JobExecutionContext] = []

    class BlockingAdapter:
        def fetch_page(
            self, _query: SourceQuery, _cursor: Cursor | None, _page_size: int
        ) -> SourcePage:
            http_started.set()
            assert release_http.wait(timeout=5)
            return source_page((source_hit("late-hit", 1),))

        def fetch_by_case_number(
            self, _process_number: str, cursor=None, page_size=100
        ) -> SourcePage:
            return self.fetch_page(query, cursor, page_size)

    collector = make_handler(BlockingAdapter())

    def capture(lease: JobLease, context: JobExecutionContext) -> JobOutcome:
        captured_contexts.append(context)
        return collector(lease, context)

    worker = LeasedWorker(
        jobs,
        worker_id="http-owner",
        handlers={"discovery": capture},
        heartbeat_interval=0.05,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run_once)
        assert http_started.wait(timeout=5)
        expire_lease(migrated_engine, job_id)
        new_lease = jobs.claim("replacement-owner")
        assert new_lease is not None
        assert captured_contexts[0].ownership_lost.wait(timeout=3)
        release_http.set()
        assert running.result(timeout=5)

    inspection = jobs.inspect(job_id)
    assert inspection.status == "running"
    assert inspection.attempt_count == 2
    assert inspection.lease_owner == "replacement-owner"
    assert inspection.checkpoint.revision == 0
    assert inspection.coverage is not None
    assert inspection.coverage["http_attempts"] == 1
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(CollectionObservation)) == 0


def test_refresh_number_paginates_every_representation_for_the_same_cnj(
    migrated_engine: Engine,
) -> None:
    query = build_query_by_case_number(CNJ)
    adapter = RecordingAdapter(
        fixture_adapter(
            query,
            (
                (source_hit("cover-one", 1), source_hit("cover-two", 2)),
                (source_hit("cover-three", 3),),
            ),
        )
    )
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query, job_type="refresh_number")
    run_worker(jobs, "refresh_number", make_handler(adapter))

    inspection = jobs.inspect(job_id)
    assert inspection.status == "completed"
    assert adapter.sizes == [2, 2, 2]
    assert inspection.coverage is not None
    assert inspection.coverage["hits_confirmed"] == 3
    assert inspection.coverage["new"] == 3
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Representation)) == 3


def test_error_after_confirmed_page_finishes_failed_with_partial_coverage(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()

    class FailingAdapter:
        calls = 0

        def fetch_page(self, _query: SourceQuery, _cursor: Cursor | None, _size: int) -> SourcePage:
            self.calls += 1
            if self.calls == 1:
                return source_page((source_hit("one", 1),))
            raise SourceError(SourceErrorCode.NETWORK, "Falha de rede simulada.")

        def fetch_by_case_number(
            self, _process_number: str, cursor=None, page_size=100
        ) -> SourcePage:
            return self.fetch_page(query, cursor, page_size)

    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    run_worker(jobs, "discovery", make_handler(FailingAdapter()))

    inspection = jobs.inspect(job_id)
    assert inspection.status == "failed"
    assert inspection.reason == "source_error"
    assert inspection.attempts[-1].error_code == SourceErrorCode.NETWORK.value
    assert inspection.coverage is not None
    assert inspection.coverage["has_persisted_data"] is True
    assert inspection.coverage["coverage"] == "partial"
    assert inspection.coverage["hits_confirmed"] == 1
    assert inspection.coverage["http_attempts"] == 2


def test_real_source_stays_blocked_until_spec003_approves_pagination(
    migrated_engine: Engine,
) -> None:
    query = discovery_query()
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query, mode="real", source="datajud")
    factory_called = False

    def source_factory(_lease: JobLease) -> Any:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("DataJud must not be called before S2 approval.")

    run_worker(jobs, "discovery", CollectionJobHandler(source_factory))
    inspection = jobs.inspect(job_id)
    assert inspection.status == "failed"
    assert inspection.reason == "capability_not_approved"
    assert inspection.attempts[-1].error_code == "DATAJUD_PAGINATION_NOT_APPROVED"
    assert not factory_called
    assert inspection.checkpoint.revision == 0


def test_owned_page_commit_rejects_a_late_possession(migrated_engine: Engine) -> None:
    query = discovery_query()
    jobs = make_jobs(migrated_engine)
    job_id = enqueue(jobs, query)
    old_lease = jobs.claim("old-worker")
    assert old_lease is not None
    expire_lease(migrated_engine, job_id)
    new_lease = jobs.claim("new-worker")
    assert new_lease is not None

    from agrojud.services.jobs import LeaseLostError

    with pytest.raises(LeaseLostError):
        jobs.commit_page(
            old_lease.job_id,
            old_lease.possession_token,
            expected_revision=0,
            budget_limit=2_000,
            source="synthetic",
            page=source_page((source_hit("one", 1),)),
        )
    inspection = jobs.inspect(job_id)
    assert inspection.checkpoint.revision == 0
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(CollectionObservation)) == 0
