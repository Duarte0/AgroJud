"""Transactional daily dispatch for saved searches and active process watches."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agrojud.config import Settings
from agrojud.db.models import (
    Job,
    Process,
    ProcessWatchlistEntry,
    Representation,
    SavedSearch,
    SavedSearchVersion,
    ScheduleDispatch,
)
from agrojud.domain.canonical_json import JSONValue, sha256_json
from agrojud.services.collection import build_collection_request
from agrojud.services.execution_groups import (
    ExecutionGroupService,
    execution_group_idempotency_key,
)
from agrojud.services.jobs import ACTIVE_STATUSES, EnqueueRequest, JobService
from agrojud.services.saved_searches import request_for_revision
from agrojud.services.schedule_time import (
    SCHEDULE_TIMEZONE,
    local_schedule_date,
    next_daily_occurrence,
)
from agrojud.sources.contracts import SourceError, SourceErrorCode, build_query_by_case_number

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
        settings: Settings | None = None,
        execution_groups: ExecutionGroupService | None = None,
    ) -> None:
        self.sessions = sessions
        self.jobs = jobs
        self.mode = mode
        self.source = source
        self.settings = settings
        self.execution_groups = execution_groups

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
            if _group_request_data(dispatch.request_snapshot) is not None:
                if self._enqueue_group_or_defer(session, dispatch, target, now=now):
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
        group_snapshot: dict[str, JSONValue] | None = None
        reason: str | None = None
        if revision is None:
            reason = "saved_search_revision_missing"
        else:
            try:
                if self._group_dispatch_enabled:
                    raw_tribunals = revision.filters.get("tribunals", ["TJGO"])
                    if not isinstance(raw_tribunals, list):
                        raise SourceError(
                            SourceErrorCode.CONTRACT,
                            "A revisão salva contém uma lista de tribunais inválida.",
                        )
                    requests: dict[str, EnqueueRequest] = {}
                    for raw_tribunal in raw_tribunals:
                        if not isinstance(raw_tribunal, str):
                            raise SourceError(
                                SourceErrorCode.CONTRACT,
                                "A revisão salva contém um tribunal inválido.",
                            )
                        candidate = request_for_revision(
                            revision,
                            search_id=search.id,
                            mode=self.mode,
                            source=self.source,
                            reference_time=now,
                            tribunal=raw_tribunal,
                        )
                        if candidate.tribunal in requests:
                            raise SourceError(
                                SourceErrorCode.CONTRACT,
                                "A revisão salva repete tribunais.",
                            )
                        requests[candidate.tribunal] = _with_schedule_metadata(
                            candidate,
                            dispatch_id=dispatch_id,
                            search_id=search.id,
                        )
                    group_snapshot = _group_snapshot(
                        kind="discovery",
                        tribunals=list(requests),
                        preset_id=revision.preset_id,
                        request_metadata={
                            "saved_search_id": str(search.id),
                            "saved_search_version": revision.version,
                            "criteria": revision.filters,
                        },
                        requests=requests,
                    )
                else:
                    request = request_for_revision(
                        revision,
                        search_id=search.id,
                        mode=self.mode,
                        source=self.source,
                        reference_time=now,
                    )
            except SourceError as error:
                reason = str(error)
        if request is not None and group_snapshot is None:
            request = _with_schedule_metadata(request, dispatch_id=dispatch_id, search_id=search.id)
        dispatch = ScheduleDispatch(
            id=dispatch_id,
            target_kind="saved_search",
            saved_search_id=search.id,
            process_id=None,
            scheduled_for_date=local_today,
            status="blocked" if reason else "pending",
            request_snapshot=(
                group_snapshot
                if group_snapshot is not None
                else _request_snapshot(request)
                if request is not None
                else None
            ),
            missed_from=missed_from,
            missed_through=missed_through,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        session.add(dispatch)
        search.next_run_at = next_daily_occurrence(now)
        search.updated_at = now
        if group_snapshot is not None:
            self._enqueue_group_or_defer(session, dispatch, search, now=now)
        elif request is not None:
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
        process_number = process.numero_cnj
        process_id = entry.process_id
        local_today = now.astimezone(SCHEDULE_TIMEZONE).date()
        missed = now > due_at
        dispatch_id = uuid4()
        group_snapshot: dict[str, JSONValue] | None = None
        request: EnqueueRequest | None = None
        if self._group_dispatch_enabled:
            tribunals = session.scalars(
                select(Representation.tribunal)
                .where(
                    Representation.process_id == process_id,
                    Representation.source == self.source,
                )
                .distinct()
                .order_by(Representation.tribunal.asc())
            ).all()
            if not tribunals:
                tribunals = ["TJGO"]
            requests: dict[str, EnqueueRequest] = {}
            for tribunal in tribunals:
                candidate = build_collection_request(
                    mode=self.mode,
                    source=self.source,
                    job_type="refresh_number",
                    query=build_query_by_case_number(process_number, tribunal=tribunal),
                )
                requests[tribunal] = _with_schedule_metadata(
                    candidate,
                    dispatch_id=dispatch_id,
                    process_id=entry.process_id,
                    process_number=process_number,
                )
            group_snapshot = _group_snapshot(
                kind="refresh_number",
                tribunals=list(requests),
                preset_id=None,
                request_metadata={
                    "process_id": str(entry.process_id),
                    "scheduled_process_number": process_number,
                },
                requests=requests,
            )
        else:
            request = build_collection_request(
                mode=self.mode,
                source=self.source,
                job_type="refresh_number",
                query=build_query_by_case_number(process_number),
            )
            request = _with_schedule_metadata(
                request,
                dispatch_id=dispatch_id,
                process_id=entry.process_id,
                process_number=process_number,
            )
        if group_snapshot is not None:
            dispatch_snapshot = group_snapshot
        elif request is not None:
            dispatch_snapshot = _request_snapshot(request)
        else:  # pragma: no cover - a request is built unless the group snapshot is present
            raise RuntimeError("O disparo do acompanhamento não contém grupo nem job.")
        dispatch = ScheduleDispatch(
            id=dispatch_id,
            target_kind="watch",
            saved_search_id=None,
            process_id=entry.process_id,
            scheduled_for_date=local_today,
            status="pending",
            request_snapshot=dispatch_snapshot,
            missed_from=local_schedule_date(due_at) if missed else None,
            missed_through=local_today if missed else None,
            reason=None,
            created_at=now,
            updated_at=now,
        )
        session.add(dispatch)
        entry.next_run_at = next_daily_occurrence(now)
        if group_snapshot is not None:
            self._enqueue_group_or_defer(session, dispatch, entry, now=now)
        elif request is not None:
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

    @property
    def _group_dispatch_enabled(self) -> bool:
        return self.execution_groups is not None

    def _enqueue_group_or_defer(
        self,
        session: Session,
        dispatch: ScheduleDispatch,
        target: SavedSearch | ProcessWatchlistEntry,
        *,
        now: datetime,
    ) -> bool:
        parsed = _group_request_data(dispatch.request_snapshot)
        if parsed is None:
            dispatch.status = "blocked"
            dispatch.reason = "invalid_pending_group_request"
            dispatch.updated_at = now
            return True
        if self.execution_groups is None:
            dispatch.status = "blocked"
            dispatch.reason = "execution_group_service_unavailable"
            dispatch.updated_at = now
            return True
        kind, tribunals, preset_id, request_snapshot, requests = parsed
        active = self._active_target_job(session, dispatch, target)
        first_request = next(iter(requests.values()), None)
        if active is not None:
            return False

        target_predicate = (
            ScheduleDispatch.saved_search_id == dispatch.saved_search_id
            if dispatch.saved_search_id is not None
            else ScheduleDispatch.process_id == dispatch.process_id
        )
        with session.no_autoflush:
            canonical = session.scalar(
                select(ScheduleDispatch)
                .where(
                    ScheduleDispatch.status == "pending",
                    ScheduleDispatch.id != dispatch.id,
                    target_predicate,
                )
                .with_for_update()
            )
        if canonical is not None:
            dispatch.status = "coalesced"
            dispatch.coalesced_into_id = canonical.id
            dispatch.updated_at = now
            canonical.request_snapshot = dispatch.request_snapshot
            canonical.missed_from = _min_date(canonical.missed_from, dispatch.missed_from)
            canonical.missed_through = _max_date(canonical.missed_through, dispatch.missed_through)
            canonical.updated_at = now
            return True

        created = self.execution_groups.create_in_session(
            session,
            kind=kind,
            mode=first_request.mode if first_request is not None else self.mode,
            source=first_request.source if first_request is not None else self.source,
            tribunals=tribunals,
            preset_id=preset_id,
            request_snapshot=request_snapshot,
            request_for_tribunal=lambda tribunal: requests[tribunal],
            idempotency_key=execution_group_idempotency_key(
                target_id=(target.id if isinstance(target, SavedSearch) else target.process_id),
                kind=kind,
                requests=tuple(requests.values()),
            ),
        )
        dispatch.status = "enqueued"
        dispatch.group_id = created.group_id
        dispatch.updated_at = now
        return True

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


def _group_snapshot(
    *,
    kind: Literal["discovery", "refresh_number"],
    tribunals: list[str],
    preset_id: str | None,
    request_metadata: dict[str, Any],
    requests: dict[str, EnqueueRequest],
) -> dict[str, JSONValue]:
    return {
        "dispatch_type": "job_group",
        "kind": kind,
        "tribunals": cast(JSONValue, tribunals),
        "preset_id": preset_id,
        "request_metadata": cast(JSONValue, request_metadata),
        "members": [_request_snapshot(request) for request in requests.values()],
    }


def _group_request_data(
    value: dict[str, Any] | None,
) -> (
    tuple[
        Literal["discovery", "refresh_number"],
        list[str],
        str | None,
        dict[str, Any],
        dict[str, EnqueueRequest],
    ]
    | None
):
    if not isinstance(value, dict) or value.get("dispatch_type") != "job_group":
        return None
    kind = value.get("kind")
    tribunals = value.get("tribunals")
    preset_id = value.get("preset_id")
    request_metadata = value.get("request_metadata")
    raw_members = value.get("members")
    if (
        kind not in ("discovery", "refresh_number")
        or not isinstance(tribunals, list)
        or any(not isinstance(item, str) for item in tribunals)
        or (preset_id is not None and not isinstance(preset_id, str))
        or not isinstance(request_metadata, dict)
        or not isinstance(raw_members, list)
    ):
        return None
    requests: dict[str, EnqueueRequest] = {}
    for raw in raw_members:
        if not isinstance(raw, dict):
            return None
        request = _request_from_snapshot(raw)
        if request is None or request.tribunal in requests:
            return None
        requests[request.tribunal] = request
    if set(requests) != set(tribunals):
        return None
    return (
        cast(Literal["discovery", "refresh_number"], kind),
        cast(list[str], tribunals),
        preset_id,
        request_metadata,
        requests,
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
