"""Transactional queue operations with database-clock leases and fencing tokens."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.models import (
    Job,
    JobAttempt,
    JobCheckpoint,
    JobEvent,
    QueueClaimState,
    SourceRateLimit,
)
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes, sha256_json
from agrojud.services.ingestion import IngestPageResult, create_collection, ingest_page
from agrojud.services.retry_policy import (
    MAX_HTTP_ATTEMPTS_PER_PAGE,
    MAX_LEASE_RECOVERIES_PER_CYCLE,
    MAX_PERSISTENCE_ATTEMPTS_PER_PAGE,
    MIN_REQUEST_INTERVAL,
)
from agrojud.sources.contracts import SourcePage

type JobType = Literal["discovery", "refresh_number", "reprocess_rules"]
type JobStatus = Literal[
    "queued", "running", "retry_wait", "completed", "partial", "failed", "cancelled"
]
type JobSessionFactory = sessionmaker[Session]

ACTIVE_STATUSES = ("queued", "running", "retry_wait")
TERMINAL_STATUSES = ("completed", "partial", "failed", "cancelled")
_ACTIVE_INDEX_PREDICATE = text("status in ('queued', 'running', 'retry_wait')")


class JobError(RuntimeError):
    """Base class for queue command failures."""


class JobNotFoundError(JobError):
    """Raised when a command refers to an unknown job."""


class InvalidJobTransitionError(JobError):
    """Raised when a command is incompatible with the persisted state."""


class LeaseLostError(JobError):
    """Raised when the current worker no longer has a valid lease."""


class JobCancellationRequestedError(JobError):
    """Raised when a running job has been asked to stop before writing more data."""


class CheckpointRevisionConflict(JobError):
    """Raised when a stale checkpoint revision attempts to overwrite progress."""


class CursorInvalidConflict(InvalidJobTransitionError):
    """Raised when exact continuation is unsafe and a new scan is required."""


class RequestDeferredError(JobError):
    """The persistent per-source limiter has not granted an HTTP request slot."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__("A próxima requisição ainda aguarda o intervalo da fonte.")
        self.retry_at = retry_at


class PageRetryExhaustedError(JobError):
    """The current page has consumed its five HTTP attempts for this cycle."""


class PersistenceRetriesExhaustedError(JobError):
    """The current page has consumed its five transient SQL persistence attempts."""


class DatabaseUnavailableError(JobError):
    """Persistence could not be confirmed; the current lease must expire naturally."""


@dataclass(frozen=True, slots=True)
class EnqueueRequest:
    mode: Literal["demo", "real"]
    job_type: JobType
    source: str
    tribunal: str
    resolved_query: JSONValue
    sort: JSONValue
    parameters: dict[str, JSONValue]
    predecessor_job_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job_id: UUID
    collection_id: UUID
    operation_key: str
    created: bool


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    collection_id: UUID | None
    job_type: JobType
    mode: Literal["demo", "real"]
    source: str
    tribunal: str
    attempt_id: UUID
    attempt_number: int
    page_attempt_count: int
    persistence_attempt_count: int
    retry_cycle: int
    worker_id: str
    possession_token: UUID
    lease_expires_at: datetime
    parameters_snapshot: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    lease_expires_at: datetime
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    cursor: JSONValue | None
    next_page: int
    revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PageCommitResult:
    checkpoint: CheckpointSnapshot
    ingestion: IngestPageResult
    page_key: str
    coverage: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    id: UUID
    attempt_number: int
    worker_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str | None
    error_code: str | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    event_number: int
    event_type: str
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobInspection:
    id: UUID
    collection_id: UUID | None
    job_type: str
    mode: str
    source: str
    tribunal: str
    operation_key: str
    parameters_snapshot: dict[str, Any]
    status: str
    coverage: dict[str, Any] | None
    reason: str | None
    cancel_requested: bool
    attempt_count: int
    retry_cycle: int
    page_attempt_count: int
    persistence_attempt_count: int
    recovery_count: int
    cursor_invalid: bool
    predecessor_job_id: UUID | None
    next_attempt_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    checkpoint: CheckpointSnapshot
    attempts: tuple[AttemptSnapshot, ...]
    events: tuple[EventSnapshot, ...]


class JobService:
    """Expose enqueue, claim, heartbeat, cancel, finish, inspect, and CAS progress."""

    def __init__(
        self,
        sessions: JobSessionFactory,
        *,
        lease_duration: timedelta = timedelta(seconds=120),
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("A duração da lease deve ser positiva.")
        self.sessions = sessions
        self.lease_duration = lease_duration

    def enqueue(self, request: EnqueueRequest) -> EnqueueResult:
        """Create a collection, job, and initial checkpoint atomically.

        Equivalent active work is coalesced by the database's partial unique index.
        Budget and other execution limits stay in the immutable snapshot but are
        intentionally absent from the operation key.
        """

        self._validate_enqueue_request(request)
        with self.sessions() as session, session.begin():
            return self.enqueue_in_session(session, request)

    def enqueue_in_session(self, session: Session, request: EnqueueRequest) -> EnqueueResult:
        """Enqueue within a caller-owned transaction, including deduplication."""

        self._validate_enqueue_request(request)
        operation = {
            "type": request.job_type,
            "source": request.source,
            "tribunal": request.tribunal,
            "query": request.resolved_query,
            "sort": request.sort,
        }
        operation_key = sha256_json(operation)
        parameters_snapshot: dict[str, JSONValue] = {
            **operation,
            "parameters": request.parameters,
        }

        savepoint = session.begin_nested()
        predecessor: Job | None = None
        previous_collection_id: UUID | None = None
        if request.predecessor_job_id is not None:
            predecessor = session.execute(
                select(Job).where(Job.id == request.predecessor_job_id).with_for_update()
            ).scalar_one_or_none()
            if predecessor is None:
                raise JobNotFoundError("A execução predecessora não existe.")
            previous_collection_id = predecessor.collection_id
        collection = create_collection(
            session,
            mode=request.mode,
            resolved_criteria=parameters_snapshot,
            previous_collection_id=previous_collection_id,
        )
        now = self._database_now(session)
        available_at = self._source_not_before(session, request.source, now)
        job_id = session.execute(
            pg_insert(Job)
            .values(
                id=uuid4(),
                collection_id=collection.id,
                job_type=request.job_type,
                mode=request.mode,
                source=request.source,
                tribunal=request.tribunal,
                operation_key=operation_key,
                parameters_snapshot=parameters_snapshot,
                status="queued",
                next_attempt_at=available_at,
                attempt_count=0,
                retry_cycle=1,
                page_attempt_count=0,
                persistence_attempt_count=0,
                recovery_count=0,
                cursor_invalid=False,
                predecessor_job_id=request.predecessor_job_id,
                event_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=[Job.operation_key], index_where=_ACTIVE_INDEX_PREDICATE
            )
            .returning(Job.id)
        ).scalar_one_or_none()

        if job_id is None:
            savepoint.rollback()
            existing = session.execute(
                select(Job)
                .where(
                    Job.operation_key == operation_key,
                    Job.status.in_(ACTIVE_STATUSES),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if existing is None:  # pragma: no cover - conflict waits for its winner
                raise RuntimeError("A execução equivalente não pôde ser localizada.")
            if existing.collection_id is None:
                raise InvalidJobTransitionError(
                    "A execução equivalente pertence a um job sem coleta."
                )
            return EnqueueResult(
                job_id=existing.id,
                collection_id=existing.collection_id,
                operation_key=operation_key,
                created=False,
            )

        session.add(
            JobCheckpoint(
                id=uuid4(),
                job_id=job_id,
                cursor=None,
                next_page=1,
                revision=0,
                updated_at=now,
            )
        )
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - inserted by this transaction
            raise RuntimeError("O job recém-criado não pôde ser lido.")
        self._append_event(
            session,
            job,
            "enqueued",
            {"job_type": request.job_type, "operation_key": operation_key},
        )
        if predecessor is not None:
            self._append_event(
                session,
                job,
                "restart_scan_started",
                {"predecessor_job_id": str(predecessor.id)},
            )
            self._append_event(
                session,
                predecessor,
                "restart_scan_spawned",
                {"job_id": str(job.id)},
            )
        savepoint.commit()
        return EnqueueResult(
            job_id=job_id,
            collection_id=collection.id,
            operation_key=operation_key,
            created=True,
        )

    def claim(
        self,
        worker_id: str,
        *,
        job_types: Sequence[str] | None = None,
        fair: bool = False,
    ) -> JobLease | None:
        """Atomically reserve ready work or recover one expired running lease."""

        worker_id = self._validate_worker_id(worker_id)
        if job_types is not None and not job_types:
            return None

        with self.sessions() as session, session.begin():
            queue_state: QueueClaimState | None = None
            categories: tuple[str, ...] = ()
            if fair:
                queue_state = session.execute(
                    select(QueueClaimState).where(QueueClaimState.key == "main").with_for_update()
                ).scalar_one_or_none()
                if queue_state is None:  # pragma: no cover - installed by the migration
                    raise RuntimeError("O estado persistente da fila não foi inicializado.")
                categories = (
                    ("refresh", "non_refresh")
                    if queue_state.last_category == "non_refresh"
                    else ("non_refresh", "refresh")
                )
            else:
                categories = ("all",)
            while True:
                now_expression = func.clock_timestamp()
                eligible = or_(
                    (
                        Job.status.in_(("queued", "retry_wait"))
                        & (Job.next_attempt_at <= now_expression)
                    ),
                    (Job.status == "running") & (Job.lease_expires_at <= now_expression),
                )
                job: Job | None = None
                claimed_category: str | None = None
                for category in categories:
                    statement = select(Job).where(eligible)
                    if job_types is not None:
                        statement = statement.where(Job.job_type.in_(job_types))
                    if category == "refresh":
                        statement = statement.where(Job.job_type == "refresh_number")
                    elif category == "non_refresh":
                        statement = statement.where(
                            Job.job_type.in_(("discovery", "reprocess_rules"))
                        )
                    statement = (
                        statement.order_by(Job.created_at, Job.id)
                        .with_for_update(skip_locked=True, of=Job)
                        .limit(1)
                    )
                    job = session.execute(statement).scalar_one_or_none()
                    if job is not None:
                        claimed_category = category
                        break
                if job is None:
                    return None

                now = self._database_now(session)
                if job.status == "running":
                    old_attempt = session.execute(
                        select(JobAttempt)
                        .where(JobAttempt.job_id == job.id, JobAttempt.finished_at.is_(None))
                        .with_for_update()
                    ).scalar_one_or_none()
                    if job.cancel_requested:
                        if old_attempt is not None:
                            self._close_attempt(
                                session,
                                old_attempt,
                                now=now,
                                outcome="cancelled",
                            )
                        job.status = "cancelled"
                        job.cancel_requested = False
                        job.reason = job.reason or "cancel_requested"
                        job.finished_at = now
                        self._clear_lease(job)
                        job.updated_at = now
                        self._append_event(
                            session,
                            job,
                            "cancelled",
                            {"attempt_number": job.attempt_count},
                        )
                        continue

                    if old_attempt is not None:
                        self._close_attempt(
                            session,
                            old_attempt,
                            now=now,
                            outcome="lease_expired",
                        )
                    job.recovery_count += 1
                    if job.recovery_count > MAX_LEASE_RECOVERIES_PER_CYCLE:
                        job.status = "failed"
                        job.reason = "recovery_exhausted"
                        job.finished_at = now
                        job.updated_at = now
                        coverage = dict(job.coverage or {})
                        coverage["query_status"] = "failed"
                        coverage["has_persisted_data"] = bool(
                            coverage.get("has_persisted_data", False)
                        )
                        job.coverage = coverage
                        self._clear_lease(job)
                        self._append_event(
                            session,
                            job,
                            "recovery_exhausted",
                            {
                                "recovery_count": job.recovery_count,
                                "limit": MAX_LEASE_RECOVERIES_PER_CYCLE,
                            },
                        )
                        continue
                    self._append_event(
                        session,
                        job,
                        "lease_expired",
                        {
                            "attempt_number": job.attempt_count,
                            "recovery_count": job.recovery_count,
                        },
                    )

                possession_token = uuid4()
                lease_expires_at = now + self.lease_duration
                job.status = "running"
                job.cancel_requested = False
                job.lease_token = possession_token
                job.lease_owner = worker_id
                job.lease_expires_at = lease_expires_at
                job.heartbeat_at = now
                job.attempt_count += 1
                job.started_at = job.started_at or now
                job.finished_at = None
                job.reason = None
                job.updated_at = now
                attempt = JobAttempt(
                    id=uuid4(),
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    worker_id=worker_id,
                    started_at=now,
                )
                session.add(attempt)
                self._append_event(
                    session,
                    job,
                    "claimed",
                    {"attempt_number": job.attempt_count, "worker_id": worker_id},
                )
                session.flush()
                if queue_state is not None:
                    queue_state.last_category = claimed_category or "non_refresh"
                    queue_state.updated_at = now
                return JobLease(
                    job_id=job.id,
                    collection_id=job.collection_id,
                    job_type=job.job_type,  # type: ignore[arg-type]
                    mode=job.mode,  # type: ignore[arg-type]
                    source=job.source,
                    tribunal=job.tribunal,
                    attempt_id=attempt.id,
                    attempt_number=job.attempt_count,
                    page_attempt_count=job.page_attempt_count,
                    persistence_attempt_count=job.persistence_attempt_count,
                    retry_cycle=job.retry_cycle,
                    worker_id=worker_id,
                    possession_token=possession_token,
                    lease_expires_at=lease_expires_at,
                    parameters_snapshot=job.parameters_snapshot,
                )

    def heartbeat(self, job_id: UUID, possession_token: UUID) -> HeartbeatResult:
        """Extend an active lease using a fresh database session and database time."""

        with self.sessions() as session, session.begin():
            job = self._lock_job(session, job_id)
            now = self._assert_lease(session, job, possession_token, allow_cancel=True)
            if job.cancel_requested:
                if job.lease_expires_at is None:  # pragma: no cover - guarded by model check
                    raise LeaseLostError("A lease ativa não tem expiração.")
                return HeartbeatResult(
                    lease_expires_at=job.lease_expires_at,
                    cancel_requested=True,
                )

            job.heartbeat_at = now
            job.lease_expires_at = now + self.lease_duration
            job.updated_at = now
            return HeartbeatResult(
                lease_expires_at=job.lease_expires_at,
                cancel_requested=False,
            )

    @contextmanager
    def owned_transaction(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        ownership_lost: Event | None = None,
    ) -> Iterator[Session]:
        """Yield a short write transaction fenced by token, lease, and cancellation.

        The job row is locked before the caller can touch representations. The
        current database clock is checked again after writes and immediately
        before SQLAlchemy commits; any expiry rolls the whole transaction back.
        """

        with self.sessions() as session, session.begin():
            self._set_transaction_timeouts(session)
            job = self._lock_job(session, job_id)
            self._assert_local_authorization(ownership_lost)
            self._assert_lease(session, job, possession_token)
            yield session
            self._assert_local_authorization(ownership_lost)
            self._assert_lease(session, job, possession_token)

    def record_http_attempt(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        expected_revision: int,
        budget_limit: int,
        ownership_lost: Event | None = None,
    ) -> int:
        """Durably count a page request before calling the external source."""

        if expected_revision < 0 or budget_limit < 1:
            raise ValueError("A revisão e o orçamento da coleta são inválidos.")
        with self.owned_transaction(
            job_id,
            possession_token,
            ownership_lost=ownership_lost,
        ) as session:
            checkpoint = session.execute(
                select(JobCheckpoint).where(JobCheckpoint.job_id == job_id).with_for_update()
            ).scalar_one_or_none()
            if checkpoint is None:  # pragma: no cover - enqueue creates it atomically
                raise RuntimeError("O checkpoint inicial do job não foi encontrado.")
            if checkpoint.revision != expected_revision:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")
            job = self._lock_job(session, job_id, lock=False)
            if job.page_attempt_count >= MAX_HTTP_ATTEMPTS_PER_PAGE:
                raise PageRetryExhaustedError("A página consumiu as cinco tentativas HTTP.")
            now = self._database_now(session)
            if job.source == "synthetic":
                source_limit = session.get(SourceRateLimit, job.source)
                if source_limit is not None and source_limit.cooldown_until is not None:
                    if source_limit.cooldown_until > now:
                        raise RequestDeferredError(source_limit.cooldown_until)
            else:
                source_limit = self._get_source_rate_limit(session, job.source, now, lock=True)
                allowed_at = max(
                    source_limit.next_request_at,
                    source_limit.cooldown_until or now,
                )
                if allowed_at > now:
                    raise RequestDeferredError(allowed_at)

                source_limit.next_request_at = now + MIN_REQUEST_INTERVAL
                source_limit.updated_at = now
            job.page_attempt_count += 1
            coverage = self._collection_progress(job.coverage, budget_limit=budget_limit)
            attempt_count = self._counter(coverage, "http_attempts") + 1
            coverage["http_attempts"] = attempt_count
            coverage["current_page_attempts"] = job.page_attempt_count
            coverage["current_page"] = checkpoint.next_page
            coverage["checkpoint_revision"] = checkpoint.revision
            job.coverage = coverage
            self._append_event(
                session,
                job,
                "http_attempted",
                {
                    "page": checkpoint.next_page,
                    "http_attempt": attempt_count,
                    "page_attempt": job.page_attempt_count,
                    "retry_cycle": job.retry_cycle,
                    "checkpoint_revision": checkpoint.revision,
                },
            )
            return attempt_count

    def record_persistence_attempt(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        expected_revision: int,
        ownership_lost: Event | None = None,
    ) -> int:
        """Persist one bounded SQL page-commit attempt before executing its writes."""

        if expected_revision < 0:
            raise ValueError("A revisão do checkpoint é inválida.")
        with self.owned_transaction(
            job_id,
            possession_token,
            ownership_lost=ownership_lost,
        ) as session:
            checkpoint = session.execute(
                select(JobCheckpoint).where(JobCheckpoint.job_id == job_id).with_for_update()
            ).scalar_one_or_none()
            if checkpoint is None:
                raise RuntimeError("O checkpoint inicial do job não foi encontrado.")
            if checkpoint.revision != expected_revision:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")
            job = self._lock_job(session, job_id, lock=False)
            if job.persistence_attempt_count >= MAX_PERSISTENCE_ATTEMPTS_PER_PAGE:
                raise PersistenceRetriesExhaustedError(
                    "A gravação da página consumiu as cinco tentativas SQL."
                )
            job.persistence_attempt_count += 1
            coverage = self._collection_progress(
                job.coverage,
                budget_limit=max(1, self._counter(job.coverage or {}, "budget_limit")),
            )
            coverage["current_page_persistence_attempts"] = job.persistence_attempt_count
            coverage["current_page"] = checkpoint.next_page
            coverage["checkpoint_revision"] = checkpoint.revision
            job.coverage = coverage
            self._append_event(
                session,
                job,
                "page_persistence_attempted",
                {
                    "page": checkpoint.next_page,
                    "persistence_attempt": job.persistence_attempt_count,
                    "retry_cycle": job.retry_cycle,
                    "checkpoint_revision": checkpoint.revision,
                },
            )
            return job.persistence_attempt_count

    def commit_page(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        expected_revision: int,
        budget_limit: int,
        source: Literal["datajud", "synthetic"],
        page: SourcePage,
        ownership_lost: Event | None = None,
    ) -> PageCommitResult:
        """Persist page data, cumulative counters, and its checkpoint atomically."""

        if expected_revision < 0 or budget_limit < 1:
            raise ValueError("A revisão e o orçamento da coleta são inválidos.")
        with self.owned_transaction(
            job_id,
            possession_token,
            ownership_lost=ownership_lost,
        ) as session:
            checkpoint = session.execute(
                select(JobCheckpoint).where(JobCheckpoint.job_id == job_id).with_for_update()
            ).scalar_one_or_none()
            if checkpoint is None:  # pragma: no cover - enqueue creates it atomically
                raise RuntimeError("O checkpoint inicial do job não foi encontrado.")
            if checkpoint.revision != expected_revision:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")
            if checkpoint.next_page < 1:
                raise RuntimeError("O checkpoint contém uma página inválida.")

            job = self._lock_job(session, job_id, lock=False)
            if job.collection_id is None:
                raise InvalidJobTransitionError(
                    "Reprocessamento local não possui coleta associada."
                )
            page_key = sha256_json(
                {
                    "collection_id": str(job.collection_id),
                    "previous_checkpoint_revision": checkpoint.revision,
                }
            )
            ingestion = ingest_page(
                session,
                collection_id=job.collection_id,
                page_key=page_key,
                source=source,
                page=page,
            )

            now = self._database_now(session)
            next_cursor: JSONValue | None = (
                list(page.cursor_final)
                if page.hits and page.cursor_final is not None
                else checkpoint.cursor
            )
            result = session.execute(
                update(JobCheckpoint)
                .where(
                    JobCheckpoint.id == checkpoint.id,
                    JobCheckpoint.revision == expected_revision,
                )
                .values(
                    cursor=next_cursor,
                    next_page=checkpoint.next_page + 1,
                    revision=expected_revision + 1,
                    updated_at=now,
                )
                .returning(
                    JobCheckpoint.cursor,
                    JobCheckpoint.next_page,
                    JobCheckpoint.revision,
                    JobCheckpoint.updated_at,
                )
            ).one_or_none()
            if result is None:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")

            coverage = self._collection_progress(job.coverage, budget_limit=budget_limit)
            coverage["pages_confirmed"] = self._counter(coverage, "pages_confirmed") + 1
            coverage["hits_confirmed"] = self._counter(coverage, "hits_confirmed") + len(page.hits)
            coverage["valid_hits"] = (
                self._counter(coverage, "valid_hits") + ingestion.valid_hit_count
            )
            coverage["rejected_hits"] = (
                self._counter(coverage, "rejected_hits") + ingestion.rejected_hit_count
            )
            coverage["new"] = self._counter(coverage, "new") + ingestion.counts.new
            coverage["updated"] = self._counter(coverage, "updated") + ingestion.counts.updated
            coverage["unchanged"] = (
                self._counter(coverage, "unchanged") + ingestion.counts.unchanged
            )
            coverage["quarantine_records"] = self._counter(coverage, "quarantine_records") + len(
                ingestion.quarantine_ids
            )
            if page.hits or "source_total" not in coverage:
                coverage["source_total"] = {
                    "present": page.total_value is not None,
                    "value": page.total_value,
                    "relation": page.total_relation,
                }
            coverage["has_persisted_data"] = bool(coverage.get("has_persisted_data") or page.hits)
            coverage["current_page"] = checkpoint.next_page
            coverage["last_page_http_attempts"] = job.page_attempt_count
            coverage["current_page_attempts"] = 0
            coverage["last_page_persistence_attempts"] = job.persistence_attempt_count
            coverage["current_page_persistence_attempts"] = 0
            coverage["checkpoint_revision"] = result.revision
            coverage["query_status"] = "exhausted" if not page.hits else "running"
            job.page_attempt_count = 0
            job.persistence_attempt_count = 0
            job.recovery_count = 0
            job.next_attempt_at = now
            job.coverage = coverage
            self._append_event(
                session,
                job,
                "page_committed",
                {
                    "page": checkpoint.next_page,
                    "hits": len(page.hits),
                    "valid_hits": ingestion.valid_hit_count,
                    "rejected_hits": ingestion.rejected_hit_count,
                    "http_attempts": coverage["last_page_http_attempts"],
                    "persistence_attempts": coverage["last_page_persistence_attempts"],
                    "checkpoint_revision": result.revision,
                },
            )
            return PageCommitResult(
                checkpoint=CheckpointSnapshot(
                    cursor=result.cursor,
                    next_page=result.next_page,
                    revision=result.revision,
                    updated_at=result.updated_at,
                ),
                ingestion=ingestion,
                page_key=page_key,
                coverage=coverage,
            )

    def advance_checkpoint(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        expected_revision: int,
        cursor: JSONValue | None,
        next_page: int,
        ownership_lost: Event | None = None,
    ) -> CheckpointSnapshot:
        """Advance progress with a revision CAS under the current job lease."""

        if expected_revision < 0:
            raise ValueError("A revisão esperada não pode ser negativa.")
        if next_page < 1:
            raise ValueError("A próxima página deve ser positiva.")
        canonical_json_bytes(cursor)

        with self.owned_transaction(
            job_id,
            possession_token,
            ownership_lost=ownership_lost,
        ) as session:
            checkpoint = session.execute(
                select(JobCheckpoint).where(JobCheckpoint.job_id == job_id).with_for_update()
            ).scalar_one_or_none()
            if checkpoint is None:  # pragma: no cover - enqueue creates it atomically
                raise RuntimeError("O checkpoint inicial do job não foi encontrado.")
            if checkpoint.revision != expected_revision:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")
            if next_page <= checkpoint.next_page:
                raise ValueError("A próxima página precisa avançar o checkpoint.")

            now = self._database_now(session)
            result = session.execute(
                update(JobCheckpoint)
                .where(
                    JobCheckpoint.id == checkpoint.id,
                    JobCheckpoint.revision == expected_revision,
                )
                .values(
                    cursor=cursor,
                    next_page=next_page,
                    revision=expected_revision + 1,
                    updated_at=now,
                )
                .returning(
                    JobCheckpoint.cursor,
                    JobCheckpoint.next_page,
                    JobCheckpoint.revision,
                    JobCheckpoint.updated_at,
                )
            ).one_or_none()
            if result is None:
                raise CheckpointRevisionConflict("A revisão do checkpoint foi alterada.")

            job = self._lock_job(session, job_id, lock=False)
            job.page_attempt_count = 0
            job.persistence_attempt_count = 0
            job.recovery_count = 0
            self._append_event(
                session,
                job,
                "checkpoint_advanced",
                {"revision": result.revision, "next_page": result.next_page},
            )
            return CheckpointSnapshot(
                cursor=result.cursor,
                next_page=result.next_page,
                revision=result.revision,
                updated_at=result.updated_at,
            )

    def request_cancel(self, job_id: UUID) -> bool:
        """Cancel queued work immediately or request cooperative stop for a lease."""

        with self.sessions() as session, session.begin():
            job = self._lock_job(session, job_id)
            now = self._database_now(session)
            if job.status in ("queued", "retry_wait"):
                job.status = "cancelled"
                job.cancel_requested = False
                job.reason = "cancel_requested"
                job.finished_at = now
                job.updated_at = now
                self._append_event(session, job, "cancelled", {"attempt_number": job.attempt_count})
                return True
            if job.status == "running":
                if job.cancel_requested:
                    return False
                job.cancel_requested = True
                job.updated_at = now
                self._append_event(
                    session,
                    job,
                    "cancel_requested",
                    {"attempt_number": job.attempt_count},
                )
                return True
            if job.status == "cancelled":
                return False
            raise InvalidJobTransitionError("O job já terminou e não pode ser cancelado.")

    def resume(self, job_id: UUID) -> UUID:
        """Start a new retry cycle from a failed or cancelled job's saved cursor."""

        try:
            with self.sessions() as session, session.begin():
                job = self._lock_job(session, job_id)
                if job.cursor_invalid:
                    raise CursorInvalidConflict(
                        "O cursor foi rejeitado pela fonte; reinicie a coleta explicitamente."
                    )
                active = self._active_equivalent(session, job.operation_key)
                if active is not None:
                    return active.id
                if job.status in ACTIVE_STATUSES:
                    return job.id
                if job.status not in ("failed", "cancelled"):
                    raise InvalidJobTransitionError(
                        "Somente jobs failed ou cancelled podem ser retomados."
                    )

                now = self._database_now(session)
                job.status = "queued"
                job.reason = None
                job.cancel_requested = False
                job.finished_at = None
                job.next_attempt_at = (
                    now
                    if job.job_type == "reprocess_rules"
                    else self._source_not_before(session, job.source, now)
                )
                job.retry_cycle += 1
                job.page_attempt_count = 0
                job.persistence_attempt_count = 0
                job.recovery_count = 0
                coverage = dict(job.coverage or {})
                coverage["current_page_attempts"] = 0
                coverage["current_page_persistence_attempts"] = 0
                job.coverage = coverage
                job.updated_at = now
                self._append_event(
                    session,
                    job,
                    "resumed",
                    {
                        "retry_cycle": job.retry_cycle,
                        "checkpoint_revision": self._checkpoint_revision(session, job.id),
                        "next_attempt_at": job.next_attempt_at.isoformat(),
                    },
                )
                return job.id
        except IntegrityError as error:
            if not self._is_active_operation_conflict(error):
                raise
            active_id = self._find_active_equivalent(job_id)
            if active_id is None:
                raise
            return active_id

    def continue_job(self, job_id: UUID, *, additional_budget: int = 2_000) -> UUID:
        """Extend a partial/limit collection while preserving its checkpoint."""

        try:
            with self.sessions() as session, session.begin():
                job = self._lock_job(session, job_id)
                if job.cursor_invalid:
                    raise CursorInvalidConflict(
                        "O cursor foi rejeitado pela fonte; somente restart_scan é seguro."
                    )
                active = self._active_equivalent(session, job.operation_key)
                if active is not None:
                    return active.id
                checkpoint = session.execute(
                    select(JobCheckpoint).where(JobCheckpoint.job_id == job.id)
                ).scalar_one()
                # Import locally because the collection handler depends on this service.
                from agrojud.services.collection import plan_limit_continuation

                plan = plan_limit_continuation(
                    status=job.status,
                    reason=job.reason,
                    cursor=checkpoint.cursor,
                    checkpoint_revision=checkpoint.revision,
                    coverage=job.coverage,
                    additional_budget=additional_budget,
                )
                now = self._database_now(session)
                previous_budget = int((job.coverage or {}).get("budget_limit", 0))
                job.status = "queued"
                job.reason = None
                job.finished_at = None
                job.next_attempt_at = self._source_not_before(session, job.source, now)
                job.page_attempt_count = 0
                job.persistence_attempt_count = 0
                job.coverage = plan.coverage
                job.coverage["current_page_attempts"] = 0
                job.coverage["current_page_persistence_attempts"] = 0
                job.updated_at = now
                self._append_event(
                    session,
                    job,
                    "continued",
                    {
                        "previous_budget": previous_budget,
                        "new_budget": plan.coverage["budget_limit"],
                        "checkpoint_revision": checkpoint.revision,
                        "continuation": plan.coverage["continuations"],
                        "next_attempt_at": job.next_attempt_at.isoformat(),
                    },
                )
                return job.id
        except IntegrityError as error:
            if not self._is_active_operation_conflict(error):
                raise
            active_id = self._find_active_equivalent(job_id)
            if active_id is None:
                raise
            return active_id

    def restart_scan(self, job_id: UUID) -> EnqueueResult:
        """Create a fresh collection linked to the previous job as its predecessor."""

        with self.sessions() as session:
            previous = session.get(Job, job_id)
            if previous is None:
                raise JobNotFoundError("O job solicitado não existe.")
            if previous.status != "failed" or not previous.cursor_invalid:
                raise InvalidJobTransitionError(
                    "Uma nova varredura exige job failed com cursor invalidado."
                )
            snapshot = dict(previous.parameters_snapshot)
            parameters = snapshot.get("parameters")
            if not isinstance(parameters, dict):
                raise InvalidJobTransitionError("O snapshot do job não contém limites válidos.")
            request = EnqueueRequest(
                mode=cast(Literal["demo", "real"], previous.mode),
                job_type=cast(JobType, previous.job_type),
                source=previous.source,
                tribunal=previous.tribunal,
                resolved_query=cast(JSONValue, snapshot.get("query")),
                sort=cast(JSONValue, snapshot.get("sort")),
                parameters=cast(dict[str, JSONValue], parameters),
                predecessor_job_id=previous.id,
            )
        result = self.enqueue(request)
        if not result.created:
            with self.sessions() as session, session.begin():
                previous = self._lock_job(session, job_id)
                self._append_event(
                    session,
                    previous,
                    "restart_scan_coalesced",
                    {"job_id": str(result.job_id)},
                )
        return result

    def finish(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        status: Literal["retry_wait", "completed", "partial", "failed", "cancelled"],
        coverage: dict[str, JSONValue] | None = None,
        reason: str | None = None,
        retry_at: datetime | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        source_cooldown_until: datetime | None = None,
        cursor_invalid: bool = False,
        ownership_lost: Event | None = None,
    ) -> JobStatus:
        """Close an attempt or place it in retry_wait without embedding retry policy."""

        if coverage is not None:
            canonical_json_bytes(coverage)
        safe_reason = self._safe_text(reason, maximum=500)
        safe_error_code = self._safe_text(error_code, maximum=80)
        safe_error_summary = self._safe_text(error_summary, maximum=240)

        with self.sessions() as session, session.begin():
            job = self._lock_job(session, job_id)
            self._assert_local_authorization(ownership_lost)
            now = self._assert_lease(session, job, possession_token, allow_cancel=True)
            effective_status: JobStatus = "cancelled" if job.cancel_requested else status
            if effective_status == "retry_wait":
                if retry_at is None:
                    raise ValueError("retry_wait exige retry_at definido pelo chamador.")
                if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                    raise ValueError("next_attempt_at precisa incluir fuso horário.")
            elif effective_status not in TERMINAL_STATUSES:
                raise ValueError("Estado final de job inválido.")

            effective_retry_at = retry_at
            source_limit: SourceRateLimit | None = None
            if source_cooldown_until is not None:
                if (
                    source_cooldown_until.tzinfo is None
                    or source_cooldown_until.utcoffset() is None
                ):
                    raise ValueError("O cooldown da fonte precisa incluir fuso horário.")
                source_limit = self._get_source_rate_limit(session, job.source, now, lock=True)
                source_limit.cooldown_until = max(
                    source_limit.cooldown_until or source_cooldown_until,
                    source_cooldown_until,
                )
                source_limit.updated_at = now
            if effective_status == "retry_wait":
                if source_limit is None:
                    source_limit = self._get_source_rate_limit(session, job.source, now, lock=True)
                if retry_at is None:  # pragma: no cover - validated above
                    raise ValueError("retry_wait exige retry_at definido pelo chamador.")
                effective_retry_at = max(
                    retry_at,
                    source_limit.next_request_at,
                    source_limit.cooldown_until or retry_at,
                )

            attempt = session.execute(
                select(JobAttempt)
                .where(JobAttempt.job_id == job_id, JobAttempt.finished_at.is_(None))
                .with_for_update()
            ).scalar_one_or_none()
            if attempt is None:
                raise InvalidJobTransitionError("O job não tem tentativa ativa para encerrar.")
            self._close_attempt(
                session,
                attempt,
                now=now,
                outcome=effective_status,
                error_code=safe_error_code
                if effective_status in ("failed", "retry_wait")
                else None,
                error_summary=safe_error_summary
                if effective_status in ("failed", "retry_wait")
                else None,
            )

            job.status = effective_status
            job.coverage = coverage
            job.cursor_invalid = job.cursor_invalid or cursor_invalid
            job.reason = "cancel_requested" if effective_status == "cancelled" else safe_reason
            job.cancel_requested = False
            job.finished_at = None if effective_status == "retry_wait" else now
            if effective_status == "retry_wait":
                if effective_retry_at is None:  # pragma: no cover - validated above
                    raise ValueError("retry_wait exige retry_at definido pelo chamador.")
                job.next_attempt_at = effective_retry_at
            job.updated_at = now
            self._clear_lease(job)
            event_type = (
                "retry_scheduled"
                if effective_status == "retry_wait"
                else "cancelled"
                if effective_status == "cancelled"
                else "finished"
            )
            self._append_event(
                session,
                job,
                event_type,
                {
                    "attempt_number": attempt.attempt_number,
                    "status": effective_status,
                    "retry_at": effective_retry_at.isoformat()
                    if effective_retry_at is not None
                    else None,
                    "cursor_invalid": job.cursor_invalid,
                    "error_code": safe_error_code
                    if effective_status in ("failed", "retry_wait")
                    else None,
                    "error_summary": safe_error_summary
                    if effective_status in ("failed", "retry_wait")
                    else None,
                },
            )
            return effective_status

    def inspect(self, job_id: UUID) -> JobInspection:
        """Read job state, checkpoint, attempts, and ordered durable events."""

        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise JobNotFoundError("O job solicitado não existe.")
            checkpoint = session.execute(
                select(JobCheckpoint).where(JobCheckpoint.job_id == job_id)
            ).scalar_one()
            attempts = session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job_id)
                .order_by(JobAttempt.attempt_number)
            ).all()
            events = session.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.event_number)
            ).all()
            return JobInspection(
                id=job.id,
                collection_id=job.collection_id,
                job_type=job.job_type,
                mode=job.mode,
                source=job.source,
                tribunal=job.tribunal,
                operation_key=job.operation_key,
                parameters_snapshot=job.parameters_snapshot,
                status=job.status,
                coverage=job.coverage,
                reason=job.reason,
                cancel_requested=job.cancel_requested,
                attempt_count=job.attempt_count,
                retry_cycle=job.retry_cycle,
                page_attempt_count=job.page_attempt_count,
                persistence_attempt_count=job.persistence_attempt_count,
                recovery_count=job.recovery_count,
                cursor_invalid=job.cursor_invalid,
                predecessor_job_id=job.predecessor_job_id,
                next_attempt_at=job.next_attempt_at,
                lease_owner=job.lease_owner,
                lease_expires_at=job.lease_expires_at,
                heartbeat_at=job.heartbeat_at,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                checkpoint=CheckpointSnapshot(
                    cursor=checkpoint.cursor,
                    next_page=checkpoint.next_page,
                    revision=checkpoint.revision,
                    updated_at=checkpoint.updated_at,
                ),
                attempts=tuple(
                    AttemptSnapshot(
                        id=attempt.id,
                        attempt_number=attempt.attempt_number,
                        worker_id=attempt.worker_id,
                        started_at=attempt.started_at,
                        finished_at=attempt.finished_at,
                        outcome=attempt.outcome,
                        error_code=attempt.error_code,
                        error_summary=attempt.error_summary,
                    )
                    for attempt in attempts
                ),
                events=tuple(
                    EventSnapshot(
                        event_number=event.event_number,
                        event_type=event.event_type,
                        details=event.details,
                        created_at=event.created_at,
                    )
                    for event in events
                ),
            )

    @staticmethod
    def _validate_enqueue_request(request: EnqueueRequest) -> None:
        if request.mode not in ("demo", "real"):
            raise ValueError("O modo da coleta deve ser demo ou real.")
        if request.job_type not in ("discovery", "refresh_number"):
            raise ValueError("Tipo de job não suportado nesta versão.")
        if not request.source.strip() or len(request.source) > 80:
            raise ValueError("A fonte deve ter entre 1 e 80 caracteres.")
        if not request.tribunal.strip() or len(request.tribunal) > 40:
            raise ValueError("O tribunal deve ter entre 1 e 40 caracteres.")
        canonical_json_bytes(request.resolved_query)
        canonical_json_bytes(request.sort)
        canonical_json_bytes(request.parameters)

    @classmethod
    def _collection_progress(
        cls,
        value: dict[str, Any] | None,
        *,
        budget_limit: int,
    ) -> dict[str, Any]:
        if value is None:
            coverage: dict[str, Any] = {}
        elif isinstance(value, dict):
            coverage = dict(value)
        else:
            raise RuntimeError("A cobertura persistida da coleta não é um objeto.")

        saved_budget = coverage.get("budget_limit")
        if saved_budget is not None:
            if (
                isinstance(saved_budget, bool)
                or not isinstance(saved_budget, int)
                or saved_budget < 1
            ):
                raise RuntimeError("O orçamento persistido da coleta é inválido.")
            budget_limit = max(budget_limit, saved_budget)
        coverage["budget_limit"] = budget_limit
        for name in (
            "pages_confirmed",
            "hits_confirmed",
            "valid_hits",
            "rejected_hits",
            "new",
            "updated",
            "unchanged",
            "quarantine_records",
            "http_attempts",
            "current_page_attempts",
            "last_page_http_attempts",
            "current_page_persistence_attempts",
            "last_page_persistence_attempts",
        ):
            coverage.setdefault(name, 0)
            cls._counter(coverage, name)
        if "has_persisted_data" not in coverage:
            coverage["has_persisted_data"] = False
        elif not isinstance(coverage["has_persisted_data"], bool):
            raise RuntimeError("O indicador de persistência da coleta é inválido.")
        return coverage

    @staticmethod
    def _counter(coverage: dict[str, Any], name: str) -> int:
        value = coverage.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"O contador persistido {name} da coleta é inválido.")
        return int(value)

    @staticmethod
    def _validate_worker_id(worker_id: str) -> str:
        if not worker_id.strip() or len(worker_id) > 120:
            raise ValueError("O identificador do worker deve ter entre 1 e 120 caracteres.")
        return worker_id

    @staticmethod
    def _safe_text(value: str | None, *, maximum: int) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())[:maximum]
        return normalized or None

    @staticmethod
    def _database_now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):  # pragma: no cover - PostgreSQL returns timestamptz
            raise RuntimeError("O relógio do PostgreSQL não retornou um horário válido.")
        return now

    def current_database_time(self) -> datetime:
        """Read PostgreSQL's clock for deterministic, cross-process retry scheduling."""

        with self.sessions() as session:
            return self._database_now(session)

    @staticmethod
    def _source_not_before(session: Session, source: str, now: datetime) -> datetime:
        source_limit = session.get(SourceRateLimit, source)
        if source_limit is None:
            return now
        return max(
            now,
            source_limit.next_request_at,
            source_limit.cooldown_until or now,
        )

    @staticmethod
    def _get_source_rate_limit(
        session: Session,
        source: str,
        now: datetime,
        *,
        lock: bool,
    ) -> SourceRateLimit:
        session.execute(
            pg_insert(SourceRateLimit)
            .values(source=source, next_request_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=[SourceRateLimit.source])
        )
        statement = select(SourceRateLimit).where(SourceRateLimit.source == source)
        if lock:
            statement = statement.with_for_update()
        return session.execute(statement).scalar_one()

    @staticmethod
    def _active_equivalent(session: Session, operation_key: str) -> Job | None:
        return session.execute(
            select(Job)
            .where(Job.operation_key == operation_key, Job.status.in_(ACTIVE_STATUSES))
            .order_by(Job.created_at, Job.id)
            .with_for_update()
            .limit(1)
        ).scalar_one_or_none()

    def _find_active_equivalent(self, job_id: UUID) -> UUID | None:
        with self.sessions() as session:
            original = session.get(Job, job_id)
            if original is None:
                raise JobNotFoundError("O job solicitado não existe.")
            active = self._active_equivalent(session, original.operation_key)
            return active.id if active is not None else None

    @staticmethod
    def _checkpoint_revision(session: Session, job_id: UUID) -> int:
        revision = session.scalar(
            select(JobCheckpoint.revision).where(JobCheckpoint.job_id == job_id)
        )
        if not isinstance(revision, int):  # pragma: no cover - enqueue creates this row atomically
            raise RuntimeError("A revisão do checkpoint não foi encontrada.")
        return revision

    @staticmethod
    def _is_active_operation_conflict(error: IntegrityError) -> bool:
        diagnostic = getattr(error.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None) == "uq_jobs_active_operation_key"

    @staticmethod
    def _set_transaction_timeouts(session: Session) -> None:
        session.execute(select(func.set_config("statement_timeout", "30000", True)))
        session.execute(select(func.set_config("lock_timeout", "5000", True)))

    @staticmethod
    def _lock_job(session: Session, job_id: UUID, *, lock: bool = True) -> Job:
        statement = select(Job).where(Job.id == job_id)
        if lock:
            statement = statement.with_for_update(of=Job)
        job = session.execute(statement).scalar_one_or_none()
        if job is None:
            raise JobNotFoundError("O job solicitado não existe.")
        return job

    def _assert_lease(
        self,
        session: Session,
        job: Job,
        possession_token: UUID,
        *,
        allow_cancel: bool = False,
    ) -> datetime:
        now = self._database_now(session)
        if (
            job.status != "running"
            or job.lease_token != possession_token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise LeaseLostError("A posse do job expirou ou foi invalidada.")
        if job.cancel_requested and not allow_cancel:
            raise JobCancellationRequestedError("O cancelamento do job foi solicitado.")
        return now

    @staticmethod
    def _assert_local_authorization(ownership_lost: Event | None) -> None:
        if ownership_lost is not None and ownership_lost.is_set():
            raise LeaseLostError("O worker suspendeu a gravação após falha no heartbeat.")

    @staticmethod
    def _clear_lease(job: Job) -> None:
        job.lease_token = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None

    @staticmethod
    def _append_event(
        session: Session,
        job: Job,
        event_type: str,
        details: dict[str, JSONValue],
    ) -> None:
        job.event_count += 1
        session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_number=job.event_count,
                event_type=event_type,
                details=details,
            )
        )

    @staticmethod
    def _close_attempt(
        session: Session,
        attempt: JobAttempt,
        *,
        now: datetime,
        outcome: str,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        attempt.finished_at = now
        attempt.outcome = outcome
        attempt.error_code = error_code
        attempt.error_summary = error_summary
