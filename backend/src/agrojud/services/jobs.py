"""Transactional queue operations with database-clock leases and fencing tokens."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.models import Job, JobAttempt, JobCheckpoint, JobEvent
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes, sha256_json
from agrojud.services.ingestion import create_collection

type JobType = Literal["discovery", "refresh_number"]
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


@dataclass(frozen=True, slots=True)
class EnqueueRequest:
    mode: Literal["demo", "real"]
    job_type: JobType
    source: str
    tribunal: str
    resolved_query: JSONValue
    sort: JSONValue
    parameters: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job_id: UUID
    collection_id: UUID
    operation_key: str
    created: bool


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    collection_id: UUID
    job_type: JobType
    attempt_id: UUID
    attempt_number: int
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
    collection_id: UUID
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

        with self.sessions() as session, session.begin():
            savepoint = session.begin_nested()
            collection = create_collection(
                session,
                mode=request.mode,
                resolved_criteria=parameters_snapshot,
            )
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
                    attempt_count=0,
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
                return EnqueueResult(
                    job_id=existing.id,
                    collection_id=existing.collection_id,
                    operation_key=operation_key,
                    created=False,
                )

            now = self._database_now(session)
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
    ) -> JobLease | None:
        """Atomically reserve ready work or recover one expired running lease."""

        worker_id = self._validate_worker_id(worker_id)
        if job_types is not None and not job_types:
            return None

        with self.sessions() as session, session.begin():
            while True:
                now_expression = func.clock_timestamp()
                eligible = or_(
                    (
                        Job.status.in_(("queued", "retry_wait"))
                        & (Job.available_at <= now_expression)
                    ),
                    (Job.status == "running") & (Job.lease_expires_at <= now_expression),
                )
                statement = (
                    select(Job)
                    .where(eligible)
                    .order_by(Job.created_at, Job.id)
                    .with_for_update(skip_locked=True, of=Job)
                    .limit(1)
                )
                if job_types is not None:
                    statement = statement.where(Job.job_type.in_(job_types))
                job = session.execute(statement).scalar_one_or_none()
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
                    self._append_event(
                        session,
                        job,
                        "lease_expired",
                        {"attempt_number": job.attempt_count},
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
                return JobLease(
                    job_id=job.id,
                    collection_id=job.collection_id,
                    job_type=job.job_type,  # type: ignore[arg-type]
                    attempt_id=attempt.id,
                    attempt_number=job.attempt_count,
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
            elif effective_status not in TERMINAL_STATUSES:
                raise ValueError("Estado final de job inválido.")

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
                error_code=safe_error_code if effective_status == "failed" else None,
                error_summary=safe_error_summary if effective_status == "failed" else None,
            )

            job.status = effective_status
            job.coverage = coverage
            job.reason = "cancel_requested" if effective_status == "cancelled" else safe_reason
            job.cancel_requested = False
            job.finished_at = None if effective_status == "retry_wait" else now
            if effective_status == "retry_wait":
                if retry_at is None:  # pragma: no cover - validated above
                    raise ValueError("retry_wait exige retry_at definido pelo chamador.")
                job.available_at = retry_at
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
                {"attempt_number": attempt.attempt_number, "status": effective_status},
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
