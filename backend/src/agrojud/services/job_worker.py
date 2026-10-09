"""Worker loop with independent heartbeat sessions and cooperative cancellation."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from threading import Event, Thread
from typing import Literal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agrojud.domain.canonical_json import JSONValue
from agrojud.services.jobs import (
    CheckpointSnapshot,
    DatabaseUnavailableError,
    JobCancellationRequestedError,
    JobLease,
    JobService,
    JobStatus,
    JobType,
    LeaseLostError,
    PageCommitResult,
)
from agrojud.sources.contracts import SourcePage


@dataclass(frozen=True, slots=True)
class JobOutcome:
    status: Literal["retry_wait", "completed", "partial", "failed", "cancelled"]
    coverage: dict[str, JSONValue] | None = None
    reason: str | None = None
    retry_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    source_cooldown_until: datetime | None = None
    cursor_invalid: bool = False


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    """Cancellation and local possession state passed to every handler."""

    jobs: JobService
    cancellation_requested: Event
    ownership_lost: Event

    def owned_transaction(self, lease: JobLease) -> AbstractContextManager[Session]:
        """Open a write transaction that also checks local heartbeat health."""

        return self.jobs.owned_transaction(
            lease.job_id,
            lease.possession_token,
            ownership_lost=self.ownership_lost,
        )

    def advance_checkpoint(
        self,
        lease: JobLease,
        *,
        expected_revision: int,
        cursor: JSONValue | None,
        next_page: int,
    ) -> CheckpointSnapshot:
        """Advance progress while propagating local heartbeat failure state."""

        return self.jobs.advance_checkpoint(
            lease.job_id,
            lease.possession_token,
            expected_revision=expected_revision,
            cursor=cursor,
            next_page=next_page,
            ownership_lost=self.ownership_lost,
        )

    def record_http_attempt(
        self,
        lease: JobLease,
        *,
        expected_revision: int,
        budget_limit: int,
    ) -> int:
        self.assert_active()
        return self.jobs.record_http_attempt(
            lease.job_id,
            lease.possession_token,
            expected_revision=expected_revision,
            budget_limit=budget_limit,
            ownership_lost=self.ownership_lost,
        )

    def record_persistence_attempt(
        self,
        lease: JobLease,
        *,
        expected_revision: int,
    ) -> int:
        """Count a bounded SQL page persistence attempt before writing the page."""

        self.assert_active()
        return self.jobs.record_persistence_attempt(
            lease.job_id,
            lease.possession_token,
            expected_revision=expected_revision,
            ownership_lost=self.ownership_lost,
        )

    def commit_page(
        self,
        lease: JobLease,
        *,
        expected_revision: int,
        budget_limit: int,
        source: Literal["datajud", "synthetic"],
        page: SourcePage,
    ) -> PageCommitResult:
        self.assert_active()
        return self.jobs.commit_page(
            lease.job_id,
            lease.possession_token,
            expected_revision=expected_revision,
            budget_limit=budget_limit,
            source=source,
            page=page,
            ownership_lost=self.ownership_lost,
        )

    def assert_active(self) -> None:
        if self.ownership_lost.is_set():
            raise LeaseLostError("O worker suspendeu a execução após falha no heartbeat.")
        if self.cancellation_requested.is_set():
            raise JobCancellationRequestedError("O cancelamento do job foi solicitado.")

    def database_now(self) -> datetime:
        """Use PostgreSQL's clock as the persistent retry schedule's time base."""

        return self.jobs.current_database_time()


type JobHandler = Callable[[JobLease, JobExecutionContext], JobOutcome]


class LeasedWorker:
    """Claim supported jobs and run handlers while a DB-only heartbeat thread renews."""

    def __init__(
        self,
        jobs: JobService,
        *,
        worker_id: str,
        handlers: Mapping[JobType, JobHandler],
        heartbeat_interval: float = 20,
        logger: logging.Logger | None = None,
    ) -> None:
        if heartbeat_interval <= 0 or heartbeat_interval >= jobs.lease_duration.total_seconds():
            raise ValueError("O intervalo de heartbeat deve ser positivo e menor que a lease.")
        self.jobs = jobs
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.heartbeat_interval = heartbeat_interval
        self.logger = logger or logging.getLogger(__name__)

    def run_once(self) -> bool:
        """Process at most one supported job; return false when the queue is idle."""

        if not self.handlers:
            return False
        lease = self.jobs.claim(self.worker_id, job_types=tuple(self.handlers), fair=True)
        if lease is None:
            return False

        stop_heartbeat = Event()
        ownership_lost = Event()
        cancellation_requested = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(lease, stop_heartbeat, ownership_lost, cancellation_requested),
            name=f"agrojud-heartbeat-{lease.job_id}",
            daemon=True,
        )
        heartbeat.start()

        outcome: JobOutcome | None = None
        handler_error = False
        persistence_unavailable = False
        context = JobExecutionContext(
            jobs=self.jobs,
            cancellation_requested=cancellation_requested,
            ownership_lost=ownership_lost,
        )
        try:
            handler = self.handlers[lease.job_type]
            outcome = handler(lease, context)
            if not isinstance(outcome, JobOutcome):
                raise TypeError("O handler deve retornar JobOutcome.")
            if outcome.status == "retry_wait" and outcome.retry_at is None:
                raise ValueError("retry_wait exige retry_at definido pelo handler.")
        except DatabaseUnavailableError as error:
            persistence_unavailable = True
            self._log(
                "job_state_not_persisted",
                job_id=str(lease.job_id),
                attempt_number=lease.attempt_number,
                error_type=type(error).__name__,
            )
        except Exception as error:
            handler_error = True
            self._log(
                "handler_failed",
                job_id=str(lease.job_id),
                attempt_number=lease.attempt_number,
                error_type=type(error).__name__,
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval * 2))
            if heartbeat.is_alive():
                ownership_lost.set()
                self._log(
                    "heartbeat_shutdown_incomplete",
                    job_id=str(lease.job_id),
                    attempt_number=lease.attempt_number,
                )

        if ownership_lost.is_set():
            self._log(
                "worker_ownership_lost",
                job_id=str(lease.job_id),
                attempt_number=lease.attempt_number,
            )
            return True
        if persistence_unavailable:
            self._log(
                "job_lease_left_to_expire",
                job_id=str(lease.job_id),
                attempt_number=lease.attempt_number,
            )
            return True

        try:
            if handler_error:
                try:
                    coverage = self.jobs.inspect(lease.job_id).coverage
                except Exception:
                    coverage = None
                status: JobStatus = self.jobs.finish(
                    lease.job_id,
                    lease.possession_token,
                    status="failed",
                    coverage=coverage,
                    reason="handler_error",
                    error_code="handler_error",
                    error_summary="Falha inesperada no handler.",
                )
            elif outcome is not None:
                status = self.jobs.finish(
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
            else:  # pragma: no cover - handler paths above either return or set an error
                return True
        except (LeaseLostError, DatabaseUnavailableError, DBAPIError) as error:
            self._log(
                "worker_state_not_confirmed",
                job_id=str(lease.job_id),
                attempt_number=lease.attempt_number,
                error_type=type(error).__name__,
            )
            return True

        self._log(
            "job_finished",
            job_id=str(lease.job_id),
            attempt_number=lease.attempt_number,
            status=status,
        )
        return True

    def _heartbeat_loop(
        self,
        lease: JobLease,
        stop: Event,
        ownership_lost: Event,
        cancellation_requested: Event,
    ) -> None:
        while not stop.wait(self.heartbeat_interval):
            try:
                result = self.jobs.heartbeat(lease.job_id, lease.possession_token)
            except Exception as error:
                ownership_lost.set()
                cancellation_requested.set()
                self._log(
                    "heartbeat_failed",
                    job_id=str(lease.job_id),
                    attempt_number=lease.attempt_number,
                    error_type=type(error).__name__,
                )
                return
            if result.cancel_requested:
                cancellation_requested.set()
                return

    def _log(self, event: str, **fields: object) -> None:
        self.logger.info(json.dumps({"event": event, **fields}, sort_keys=True))
