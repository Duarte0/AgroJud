"""Transactional daily dispatch for saved searches and active process watches."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.models import (
    Job,
    Process,
    ProcessWatchlistEntry,
    SavedSearch,
    SavedSearchVersion,
    ScheduleDispatch,
)
from agrojud.domain.canonical_json import JSONValue, sha256_json
from agrojud.services.collection import build_collection_request
from agrojud.services.jobs import ACTIVE_STATUSES, EnqueueRequest, JobService
from agrojud.services.saved_searches import request_for_revision
from agrojud.services.schedule_time import (
    SCHEDULE_TIMEZONE,
    local_schedule_date,
    next_daily_occurrence,
)
from agrojud.sources.contracts import SourceError, build_query_by_case_number

SCHEDULER_INTERVAL_SECONDS = 30
type EnvironmentMode = Literal["demo", "real"]


class DailyScheduler:
    """Reserve due targets and enqueue at most one active scheduled job per target."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        jobs: JobService,
        *,
        mode: EnvironmentMode,
        source: str,
    ) -> None:
        self.sessions = sessions
        self.jobs = jobs
        self.mode = mode
        self.source = source

    def run_due(self, *, reference_time: datetime | None = None, limit: int = 100) -> int:
        """Dispatch due work and retry pending target evaluations in one transaction."""

        if limit < 1:
            raise ValueError("O limite de disparos precisa ser positivo.")
        with self.sessions() as session, session.begin():
            now = reference_time or self._database_now(session)
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("O relógio do scheduler precisa conter fuso horário.")
            now = now.astimezone(UTC)
            handled = self._evaluate_pending(session, now=now, limit=limit)
            due_searches = session.scalars(
                select(SavedSearch)
                .where(SavedSearch.enabled.is_(True), SavedSearch.next_run_at <= now)
                .order_by(SavedSearch.next_run_at, SavedSearch.id)
                .with_for_update(skip_locked=True)
                .limit(max(0, limit - handled))
            ).all()
            for search in due_searches:
                self._dispatch_search(session, search, now=now)
                handled += 1

            remaining = max(0, limit - handled)
            due_watches = session.scalars(
                select(ProcessWatchlistEntry)
                .where(
                    ProcessWatchlistEntry.active.is_(True),
                    ProcessWatchlistEntry.next_run_at <= now,
                )
                .order_by(ProcessWatchlistEntry.next_run_at, ProcessWatchlistEntry.process_id)
                .with_for_update(skip_locked=True)
                .limit(remaining)
            ).all()
            for entry in due_watches:
                self._dispatch_watch(session, entry, now=now)
                handled += 1
            return handled

    def _evaluate_pending(self, session: Session, *, now: datetime, limit: int) -> int:
        pending = session.scalars(
            select(ScheduleDispatch)
            .where(ScheduleDispatch.status == "pending")
            .order_by(ScheduleDispatch.created_at, ScheduleDispatch.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        handled = 0
        for dispatch in pending:
            target = self._lock_target(session, dispatch)
            if target is None or not self._target_enabled(target):
                dispatch.status = "cancelled"
                dispatch.reason = "target_disabled"
                dispatch.updated_at = now
                handled += 1
                continue
            active = self._active_target_job(session, dispatch, target)
            request = _request_from_snapshot(dispatch.request_snapshot)
            if request is None:
                dispatch.status = "blocked"
                dispatch.reason = "invalid_pending_request"
                dispatch.updated_at = now
                handled += 1
                continue
            expected_key = _operation_key(request)
            if active is not None and active.operation_key != expected_key:
                continue
            result = self.jobs.enqueue_in_session(session, request)
            dispatch.status = "enqueued"
            dispatch.job_id = result.job_id
            dispatch.updated_at = now
            handled += 1
        return handled

    def _dispatch_search(self, session: Session, search: SavedSearch, *, now: datetime) -> None:
        due_at = search.next_run_at
        if due_at is None:  # pragma: no cover - guarded by the database constraint
            return
        local_today = now.astimezone(SCHEDULE_TIMEZONE).date()
        due_date = local_schedule_date(due_at)
        missed = now > due_at
        missed_from = due_date if missed else None
        missed_through = local_today if missed else None
        revision = session.scalar(
            select(SavedSearchVersion).where(
                SavedSearchVersion.saved_search_id == search.id,
                SavedSearchVersion.version == search.current_version,
            )
        )
        dispatch_id = uuid4()
        request: EnqueueRequest | None = None
        reason: str | None = None
        if revision is None:
            reason = "saved_search_revision_missing"
        else:
            try:
                request = request_for_revision(
                    revision,
                    search_id=search.id,
                    mode=self.mode,
                    source=self.source,
                    reference_time=now,
                )
            except SourceError as error:
                reason = str(error)
        if request is not None:
            request = _with_schedule_metadata(request, dispatch_id=dispatch_id, search_id=search.id)
        dispatch = ScheduleDispatch(
            id=dispatch_id,
            target_kind="saved_search",
            saved_search_id=search.id,
            process_id=None,
            scheduled_for_date=local_today,
            status="blocked" if reason else "pending",
            request_snapshot=_request_snapshot(request) if request is not None else None,
            missed_from=missed_from,
            missed_through=missed_through,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        session.add(dispatch)
        search.next_run_at = next_daily_occurrence(now)
        search.updated_at = now
        if request is not None:
            self._enqueue_or_defer(session, dispatch, request, search)

    def _dispatch_watch(
        self,
        session: Session,
        entry: ProcessWatchlistEntry,
        *,
        now: datetime,
    ) -> None:
        due_at = entry.next_run_at
        if due_at is None:  # pragma: no cover - guarded by the database constraint
            return
        process = session.get(Process, entry.process_id)
        if process is None:  # pragma: no cover - protected by the watchlist foreign key
            return
        local_today = now.astimezone(SCHEDULE_TIMEZONE).date()
        missed = now > due_at
        dispatch_id = uuid4()
        request = build_collection_request(
            mode=self.mode,
            source=self.source,
            job_type="refresh_number",
            query=build_query_by_case_number(process.numero_cnj),
        )
        request = _with_schedule_metadata(
            request,
            dispatch_id=dispatch_id,
            process_id=entry.process_id,
            process_number=process.numero_cnj,
        )
        dispatch = ScheduleDispatch(
            id=dispatch_id,
            target_kind="watch",
            saved_search_id=None,
            process_id=entry.process_id,
            scheduled_for_date=local_today,
            status="pending",
            request_snapshot=_request_snapshot(request),
            missed_from=local_schedule_date(due_at) if missed else None,
            missed_through=local_today if missed else None,
            reason=None,
            created_at=now,
            updated_at=now,
        )
        session.add(dispatch)
        entry.next_run_at = next_daily_occurrence(now)
        self._enqueue_or_defer(session, dispatch, request, entry)

    def _enqueue_or_defer(
        self,
        session: Session,
        dispatch: ScheduleDispatch,
        request: EnqueueRequest,
        target: SavedSearch | ProcessWatchlistEntry,
    ) -> None:
        with session.no_autoflush:
            active = self._active_target_job(session, dispatch, target)
            target_predicate = (
                ScheduleDispatch.saved_search_id == dispatch.saved_search_id
                if dispatch.saved_search_id is not None
                else ScheduleDispatch.process_id == dispatch.process_id
            )
            canonical = session.scalar(
                select(ScheduleDispatch)
                .where(
                    ScheduleDispatch.status == "pending",
                    ScheduleDispatch.id != dispatch.id,
                    target_predicate,
                )
                .with_for_update()
            )
        expected_key = _operation_key(request)
        if active is not None and active.operation_key != expected_key:
            if canonical is None:
                dispatch.status = "pending"
                return
            dispatch.status = "coalesced"
            dispatch.coalesced_into_id = canonical.id
            dispatch.request_snapshot = _request_snapshot(request)
            canonical_request = _with_schedule_metadata(
                request,
                dispatch_id=canonical.id,
                search_id=dispatch.saved_search_id,
            )
            canonical.request_snapshot = _request_snapshot(canonical_request)
            canonical.missed_from = _min_date(canonical.missed_from, dispatch.missed_from)
            canonical.missed_through = _max_date(canonical.missed_through, dispatch.missed_through)
            canonical.updated_at = dispatch.updated_at
            return
        if active is not None and canonical is not None:
            canonical.status = "enqueued"
            canonical.job_id = active.id
            canonical.updated_at = dispatch.updated_at
        result = self.jobs.enqueue_in_session(session, request)
        dispatch.status = "enqueued"
        dispatch.job_id = result.job_id

    def _active_target_job(
        self,
        session: Session,
        dispatch: ScheduleDispatch,
        target: SavedSearch | ProcessWatchlistEntry,
    ) -> Job | None:
        statement = select(Job).where(Job.status.in_(ACTIVE_STATUSES))
        if isinstance(target, SavedSearch):
            statement = statement.where(
                Job.job_type == "discovery",
                Job.parameters_snapshot["parameters"]["saved_search_id"].astext == str(target.id),
            )
        else:
            process = session.get(Process, target.process_id)
            if process is None:
                return None
            statement = statement.where(
                Job.job_type == "refresh_number",
                Job.parameters_snapshot["query"]["process_number"].astext == process.numero_cnj,
            )
        return session.execute(
            statement.order_by(Job.created_at, Job.id).with_for_update().limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _lock_target(
        session: Session, dispatch: ScheduleDispatch
    ) -> SavedSearch | ProcessWatchlistEntry | None:
        if dispatch.saved_search_id is not None:
            return session.execute(
                select(SavedSearch)
                .where(SavedSearch.id == dispatch.saved_search_id)
                .with_for_update()
            ).scalar_one_or_none()
        if dispatch.process_id is not None:
            return session.execute(
                select(ProcessWatchlistEntry)
                .where(ProcessWatchlistEntry.process_id == dispatch.process_id)
                .with_for_update()
            ).scalar_one_or_none()
        return None

    @staticmethod
    def _target_enabled(target: SavedSearch | ProcessWatchlistEntry) -> bool:
        if isinstance(target, SavedSearch):
            return target.enabled
        return target.active

    @staticmethod
    def _database_now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if now is None:  # pragma: no cover - PostgreSQL always returns a timestamp
            raise RuntimeError("O banco não retornou o horário do scheduler.")
        return cast(datetime, now)


def _with_schedule_metadata(
    request: EnqueueRequest,
    *,
    dispatch_id: UUID,
    search_id: UUID | None = None,
    process_id: UUID | None = None,
    process_number: str | None = None,
) -> EnqueueRequest:
    parameters: dict[str, JSONValue] = {
        **request.parameters,
        "schedule_dispatch_id": str(dispatch_id),
    }
    if search_id is not None:
        parameters["saved_search_id"] = str(search_id)
    if process_id is not None:
        parameters["scheduled_process_id"] = str(process_id)
    if process_number is not None:
        parameters["scheduled_process_number"] = process_number
    return EnqueueRequest(
        mode=request.mode,
        job_type=request.job_type,
        source=request.source,
        tribunal=request.tribunal,
        resolved_query=request.resolved_query,
        sort=request.sort,
        parameters=parameters,
        predecessor_job_id=request.predecessor_job_id,
    )


def _request_snapshot(request: EnqueueRequest) -> dict[str, JSONValue]:
    return {
        "mode": request.mode,
        "job_type": request.job_type,
        "source": request.source,
        "tribunal": request.tribunal,
        "resolved_query": request.resolved_query,
        "sort": request.sort,
        "parameters": request.parameters,
        "predecessor_job_id": (
            str(request.predecessor_job_id) if request.predecessor_job_id is not None else None
        ),
    }


def _request_from_snapshot(value: dict[str, Any] | None) -> EnqueueRequest | None:
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    job_type = value.get("job_type")
    source = value.get("source")
    tribunal = value.get("tribunal")
    query = value.get("resolved_query")
    sort = value.get("sort")
    parameters = value.get("parameters")
    predecessor_id = value.get("predecessor_job_id")
    if (
        mode not in ("demo", "real")
        or job_type not in ("discovery", "refresh_number")
        or not isinstance(source, str)
        or not isinstance(tribunal, str)
        or not isinstance(query, dict)
        or not isinstance(sort, list)
        or not isinstance(parameters, dict)
        or (predecessor_id is not None and not isinstance(predecessor_id, str))
    ):
        return None
    try:
        predecessor = UUID(predecessor_id) if predecessor_id else None
    except ValueError:
        return None
    return EnqueueRequest(
        mode=cast(Any, mode),
        job_type=cast(Any, job_type),
        source=source,
        tribunal=tribunal,
        resolved_query=cast(JSONValue, query),
        sort=cast(JSONValue, sort),
        parameters=cast(dict[str, JSONValue], parameters),
        predecessor_job_id=predecessor,
    )


def _operation_key(request: EnqueueRequest) -> str:
    return sha256_json(
        {
            "type": request.job_type,
            "source": request.source,
            "tribunal": request.tribunal,
            "query": request.resolved_query,
            "sort": request.sort,
        }
    )


def _min_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
