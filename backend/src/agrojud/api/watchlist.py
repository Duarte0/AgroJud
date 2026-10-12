"""Manual process watchlist and gated exact-number refresh commands."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.job_groups import _group_response
from agrojud.api.jobs import SourceCapabilityUnavailable, _source_for_environment
from agrojud.api.schedule_status import schedule_dispatch_response
from agrojud.api.schemas import (
    ExecutionGroupResponse,
    JobStatus,
    PaginationResponse,
    ProcessRefreshResultResponse,
    ProcessWatchHistoryResponse,
    ProcessWatchResponse,
    RepresentationBaselineResponse,
    WatchlistItemResponse,
)
from agrojud.config import Settings
from agrojud.db.models import (
    Job,
    Process,
    ProcessWatchlistEntry,
    ProcessWatchlistHistory,
    Representation,
    ScheduleDispatch,
)
from agrojud.services.collection import build_collection_request
from agrojud.services.execution_groups import execution_group_idempotency_key
from agrojud.services.jobs import EnqueueRequest
from agrojud.services.news import watch_baselines
from agrojud.services.watchlist import lock_process_watch, set_process_watch
from agrojud.sources.contracts import build_query_by_case_number
from agrojud.sources.factory import real_source_unavailable_reason

router = APIRouter(prefix="/api/v1", tags=["watchlist"])
_ACTIVE_JOB_STATUSES = ("queued", "running", "retry_wait")


@router.get(
    "/watchlist",
    response_model=PaginationResponse[WatchlistItemResponse],
    responses=ERROR_RESPONSES,
    summary="Lista os processos acompanhados",
)
def list_watchlist(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> PaginationResponse[WatchlistItemResponse]:
    with request.app.state.session_factory() as session:
        total = (
            session.scalar(
                select(func.count())
                .select_from(ProcessWatchlistEntry)
                .where(ProcessWatchlistEntry.active.is_(True))
            )
            or 0
        )
        entries = session.execute(
            select(ProcessWatchlistEntry, Process)
            .join(Process, Process.id == ProcessWatchlistEntry.process_id)
            .where(ProcessWatchlistEntry.active.is_(True))
            .order_by(
                ProcessWatchlistEntry.included_at.desc(),
                ProcessWatchlistEntry.process_id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        refreshes = _latest_refreshes(
            session,
            [process.numero_cnj for _entry, process in entries],
            settings=request.app.state.settings,
        )
        schedules = _latest_watch_schedules(session, [entry.process_id for entry, _ in entries])
        items = [
            WatchlistItemResponse(
                process_id=process.id,
                numero_cnj=process.numero_cnj,
                included_at=entry.included_at,
                next_run_at=cast(datetime, entry.next_run_at),
                last_refresh=_most_recent_refresh(refreshes.get(process.numero_cnj, [])),
                refreshes=refreshes.get(process.numero_cnj, []),
                last_schedule=schedule_dispatch_response(schedules.get(entry.process_id)),
            )
            for entry, process in entries
        ]
    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


@router.get(
    "/processes/{process_id}/watch",
    response_model=ProcessWatchResponse,
    responses=ERROR_RESPONSES,
    summary="Consulta o acompanhamento e seu histórico",
)
def get_process_watch(process_id: UUID, request: Request) -> ProcessWatchResponse:
    with request.app.state.session_factory() as session:
        process = session.get(Process, process_id)
        if process is None:
            raise HTTPException(status_code=404)
        return _watch_response(session, process, settings=request.app.state.settings)


@router.put(
    "/processes/{process_id}/watch",
    response_model=ProcessWatchResponse,
    responses=ERROR_RESPONSES,
    summary="Inclui ou reinclui um processo nos acompanhados",
)
def include_process_watch(process_id: UUID, request: Request) -> ProcessWatchResponse:
    with request.app.state.session_factory() as session, session.begin():
        process, _entry = set_process_watch(session, process_id, active=True)
        if process is None:
            raise HTTPException(status_code=404)
        response = _watch_response(session, process, settings=request.app.state.settings)
    return response


@router.delete(
    "/processes/{process_id}/watch",
    response_model=ProcessWatchResponse,
    responses=ERROR_RESPONSES,
    summary="Remove um processo dos acompanhados sem apagar seus dados",
)
def remove_process_watch(process_id: UUID, request: Request) -> ProcessWatchResponse:
    with request.app.state.session_factory() as session, session.begin():
        process, _entry = set_process_watch(session, process_id, active=False)
        if process is None:
            raise HTTPException(status_code=404)
        response = _watch_response(session, process, settings=request.app.state.settings)
    return response


@router.post(
    "/processes/{process_id}/refresh",
    response_model=ExecutionGroupResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Enfileira atualização independente por tribunal do processo acompanhado",
)
def refresh_process(
    process_id: UUID,
    request: Request,
    response: Response,
) -> ExecutionGroupResponse:
    settings = request.app.state.settings
    mode, source = _source_for_environment(settings)
    unavailable = real_source_unavailable_reason(settings)
    if unavailable is not None:
        raise SourceCapabilityUnavailable(unavailable)

    with request.app.state.session_factory() as session, session.begin():
        process, entry = lock_process_watch(session, process_id=process_id)
        if process is None:
            raise HTTPException(status_code=404)
        if entry is None or not entry.active:
            raise HTTPException(status_code=409)
        process_number = process.numero_cnj
        watched_process_id = process.id
        tribunals = session.scalars(
            select(Representation.tribunal)
            .where(
                Representation.process_id == watched_process_id,
                Representation.source == source,
            )
            .distinct()
            .order_by(Representation.tribunal.asc())
        ).all()
        if not tribunals:
            tribunals = ["TJGO"]

        def request_for(tribunal: str) -> EnqueueRequest:
            return build_collection_request(
                mode=mode,
                source=source,
                job_type="refresh_number",
                query=build_query_by_case_number(process_number, tribunal=tribunal),
            )

        member_requests = {tribunal: request_for(tribunal) for tribunal in tribunals}
        created = request.app.state.execution_groups.create_in_session(
            session,
            kind="refresh_number",
            mode=mode,
            source=source,
            tribunals=tribunals,
            preset_id=None,
            request_snapshot={"process_id": str(watched_process_id)},
            request_for_tribunal=lambda tribunal: member_requests[tribunal],
            idempotency_key=execution_group_idempotency_key(
                target_id=watched_process_id,
                kind="refresh_number",
                requests=tuple(member_requests.values()),
            ),
        )
        view = request.app.state.execution_groups.inspect_in_session(session, created.group_id)
        if view is None:  # pragma: no cover - inserted in this transaction
            raise RuntimeError("O grupo de atualização não pôde ser lido.")
        group_response = _group_response(view)
    response.headers["Location"] = str(
        request.url_for("get_job_group", group_id=str(group_response.id))
    )
    return group_response


def _watch_response(
    session: Session,
    process: Process,
    *,
    settings: Settings,
) -> ProcessWatchResponse:
    entry = session.get(ProcessWatchlistEntry, process.id)
    events = session.scalars(
        select(ProcessWatchlistHistory)
        .where(ProcessWatchlistHistory.process_id == process.id)
        .order_by(ProcessWatchlistHistory.created_at.asc(), ProcessWatchlistHistory.id.asc())
    ).all()
    refreshes = _latest_refreshes(session, [process.numero_cnj], settings=settings)
    return ProcessWatchResponse(
        process_id=process.id,
        active=entry.active if entry is not None else False,
        included_at=entry.included_at if entry is not None else None,
        removed_at=entry.removed_at if entry is not None else None,
        next_run_at=entry.next_run_at if entry is not None else None,
        last_schedule=schedule_dispatch_response(
            _latest_watch_schedules(session, [process.id]).get(process.id)
        ),
        history=[
            ProcessWatchHistoryResponse(
                id=event.id,
                action=cast(Literal["included", "removed"], event.action),
                created_at=event.created_at,
            )
            for event in events
        ],
        baselines=(
            [
                RepresentationBaselineResponse(
                    representation_id=baseline.representation_id,
                    source=representation.source,
                    tribunal=representation.tribunal,
                    state=cast(Literal["pending", "established"], baseline.state),
                    version_id=baseline.version_id,
                    established_at=baseline.established_at,
                )
                for baseline, representation in watch_baselines(session, process_id=process.id)
            ]
            if entry is not None and entry.active
            else []
        ),
        last_refresh=_most_recent_refresh(refreshes.get(process.numero_cnj, [])),
        refreshes=refreshes.get(process.numero_cnj, []),
    )


def _latest_watch_schedules(
    session: Session,
    process_ids: list[UUID],
) -> dict[UUID, ScheduleDispatch]:
    if not process_ids:
        return {}
    dispatches = session.scalars(
        select(ScheduleDispatch)
        .where(ScheduleDispatch.process_id.in_(process_ids))
        .order_by(
            ScheduleDispatch.scheduled_for_date.desc(),
            ScheduleDispatch.created_at.desc(),
            ScheduleDispatch.id.desc(),
        )
    ).all()
    latest: dict[UUID, ScheduleDispatch] = {}
    for dispatch in dispatches:
        if dispatch.process_id is not None:
            latest.setdefault(dispatch.process_id, dispatch)
    return latest


def _latest_refreshes(
    session: Session,
    process_numbers: list[str],
    *,
    settings: Settings,
) -> dict[str, list[ProcessRefreshResultResponse]]:
    if not process_numbers:
        return {}

    mode, source = _source_for_environment(settings)
    query_number = Job.parameters_snapshot["query"]["process_number"].astext
    jobs = session.scalars(
        select(Job)
        .where(
            Job.job_type == "refresh_number",
            Job.mode == mode,
            Job.source == source,
            query_number.in_(process_numbers),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    ).all()

    latest: dict[str, dict[str, ProcessRefreshResultResponse]] = {}
    for job in jobs:
        snapshot = job.parameters_snapshot.get("query")
        number = snapshot.get("process_number") if isinstance(snapshot, dict) else None
        if not isinstance(number, str):
            continue
        by_tribunal = latest.setdefault(number, {})
        if job.tribunal in by_tribunal:
            continue
        hit_count = _hit_count(job.coverage)
        if job.status in _ACTIVE_JOB_STATUSES:
            state: Literal[
                "pending", "found", "absent_in_query", "partial", "failed", "cancelled"
            ] = "pending"
        elif job.status == "partial":
            state = "partial"
        elif job.status == "failed":
            state = "failed"
        elif job.status == "cancelled":
            state = "cancelled"
        elif job.status == "completed" and hit_count is not None:
            state = "found" if hit_count > 0 else "absent_in_query"
        else:
            # A completed row without confirmed-hit evidence cannot prove absence.
            state = "partial"

        by_tribunal[job.tribunal] = ProcessRefreshResultResponse(
            job_id=job.id,
            tribunal=job.tribunal,
            state=state,
            job_status=cast(JobStatus, job.status),
            checked_at=job.finished_at or job.created_at,
            hit_count=hit_count,
        )
    return {
        number: sorted(results.values(), key=lambda result: result.tribunal)
        for number, results in latest.items()
    }


def _most_recent_refresh(
    refreshes: list[ProcessRefreshResultResponse],
) -> ProcessRefreshResultResponse | None:
    return max(refreshes, key=lambda result: result.checked_at, default=None)


def _hit_count(coverage: dict[str, Any] | None) -> int | None:
    if coverage is None:
        return None
    value = coverage.get("hits_confirmed")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
