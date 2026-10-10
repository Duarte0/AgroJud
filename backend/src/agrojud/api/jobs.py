"""HTTP endpoints for the durable job queue."""

from __future__ import annotations

from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    CreateJobRequest,
    DiscoveryJobRequest,
    JobAttemptResponse,
    JobCheckpointResponse,
    JobCommandResponse,
    JobCreatedResponse,
    JobDetailResponse,
    JobEventResponse,
    JobStatus,
    JobSummaryResponse,
    PaginationResponse,
    RefreshNumberJobRequest,
)
from agrojud.config import Settings
from agrojud.db.models import Job
from agrojud.services.collection import build_collection_request
from agrojud.services.jobs import InvalidJobTransitionError, JobInspection, JobService
from agrojud.services.watchlist import lock_process_watch
from agrojud.sources.catalog import compile_preset
from agrojud.sources.contracts import SourceError, build_query_by_case_number
from agrojud.sources.factory import real_source_unavailable_reason

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
_ACTIVE_STATUSES = ("queued", "running", "retry_wait")


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Enfileira uma coleta tipada",
)
def create_job(
    body: CreateJobRequest,
    request: Request,
    response: Response,
) -> JobCreatedResponse:
    settings: Settings = request.app.state.settings
    mode, source = _source_for_environment(settings)
    unavailable = real_source_unavailable_reason(settings)
    if unavailable is not None:
        raise SourceCapabilityUnavailable(unavailable)

    if isinstance(body, DiscoveryJobRequest):
        compiled = compile_preset(
            body.criteria.preset_id,
            environment=mode,
            filed_from=body.criteria.filed_from,
            filed_through_inclusive=body.criteria.filed_through,
        )
        enqueue_request = build_collection_request(
            mode=mode,
            source=source,
            job_type=body.kind,
            query=compiled.query,
            catalog_snapshot=compiled.snapshot(),
            page_size=body.criteria.page_size,
            hit_budget=body.criteria.hit_budget,
        )
    elif isinstance(body, RefreshNumberJobRequest):
        query = build_query_by_case_number(body.criteria.process_number)
        enqueue_request = build_collection_request(
            mode=mode,
            source=source,
            job_type=body.kind,
            query=query,
            page_size=body.criteria.page_size,
            hit_budget=body.criteria.hit_budget,
        )
        if query.process_number is None:  # pragma: no cover - builder guarantees a CNJ number
            raise RuntimeError("A consulta por número não contém o CNJ normalizado.")
        with request.app.state.session_factory() as session, session.begin():
            process, entry = lock_process_watch(session, process_number=query.process_number)
            if process is None:
                raise HTTPException(status_code=404)
            if entry is None or not entry.active:
                raise HTTPException(status_code=409)
            result = request.app.state.jobs.enqueue(enqueue_request)
    else:  # pragma: no cover - discriminator validation owns this boundary
        raise AssertionError("Tipo de job não suportado.")

    if isinstance(body, DiscoveryJobRequest):
        result = request.app.state.jobs.enqueue(enqueue_request)
    inspection = request.app.state.jobs.inspect(result.job_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(result.job_id)))
    return JobCreatedResponse(
        job_id=result.job_id,
        collection_id=result.collection_id,
        status=cast(JobStatus, inspection.status),
        reused=not result.created,
    )


@router.get(
    "",
    response_model=PaginationResponse[JobSummaryResponse],
    responses=ERROR_RESPONSES,
    summary="Lista jobs persistidos",
)
def list_jobs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status: str | None = Query(
        default=None,
        pattern="^(queued|running|retry_wait|completed|partial|failed|cancelled)$",
    ),
    kind: str | None = Query(default=None, pattern="^(discovery|refresh_number)$"),
) -> PaginationResponse[JobSummaryResponse]:
    filters: list[ColumnElement[bool]] = [Job.job_type.in_(("discovery", "refresh_number"))]
    if status is not None:
        filters.append(Job.status == status)
    if kind is not None:
        filters.append(Job.job_type == kind)

    with request.app.state.session_factory() as session:
        total = session.scalar(select(func.count()).select_from(Job).where(*filters)) or 0
        jobs = session.scalars(
            select(Job)
            .where(*filters)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = [_summary_from_row(job) for job in jobs]

    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


@router.get(
    "/{job_id}",
    name="get_job",
    response_model=JobDetailResponse,
    responses=ERROR_RESPONSES,
    summary="Consulta o estado e progresso de um job",
)
def get_job(job_id: UUID, request: Request) -> JobDetailResponse:
    inspection: JobInspection = request.app.state.jobs.inspect(job_id)
    _require_collection_job(inspection)
    return _detail_from_inspection(inspection)


@router.post(
    "/{job_id}/cancel",
    response_model=JobCommandResponse,
    responses=ERROR_RESPONSES,
    summary="Cancela ou solicita o cancelamento cooperativo",
)
def cancel_job(job_id: UUID, request: Request) -> JobCommandResponse:
    jobs: JobService = request.app.state.jobs
    _require_collection_job(jobs.inspect(job_id))
    jobs.request_cancel(job_id)
    return _command_from_inspection(jobs.inspect(job_id), reused=False)


@router.post(
    "/{job_id}/resume",
    response_model=JobCommandResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Retoma um job falho ou cancelado",
)
def resume_job(job_id: UUID, request: Request, response: Response) -> JobCommandResponse:
    jobs: JobService = request.app.state.jobs
    before = jobs.inspect(job_id)
    _require_collection_job(before)
    resumed_id = jobs.resume(job_id)
    inspection = jobs.inspect(resumed_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(resumed_id)))
    return _command_from_inspection(
        inspection,
        reused=resumed_id != job_id or before.status in _ACTIVE_STATUSES,
    )


@router.post(
    "/{job_id}/continue",
    response_model=JobCommandResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Continua uma coleta parcial que atingiu o orçamento",
)
def continue_job(job_id: UUID, request: Request, response: Response) -> JobCommandResponse:
    jobs: JobService = request.app.state.jobs
    before = jobs.inspect(job_id)
    _require_collection_job(before)
    try:
        continued_id = jobs.continue_job(job_id)
    except SourceError as error:
        if error.code.value == "VALIDATION":
            raise InvalidJobTransitionError(str(error)) from error
        raise
    inspection = jobs.inspect(continued_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(continued_id)))
    return _command_from_inspection(
        inspection,
        reused=continued_id != job_id or before.status in _ACTIVE_STATUSES,
    )


@router.post(
    "/{job_id}/restart-scan",
    response_model=JobCommandResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Inicia nova varredura após invalidação do cursor",
)
def restart_scan(job_id: UUID, request: Request, response: Response) -> JobCommandResponse:
    jobs: JobService = request.app.state.jobs
    _require_collection_job(jobs.inspect(job_id))
    result = jobs.restart_scan(job_id)
    inspection = jobs.inspect(result.job_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(result.job_id)))
    return _command_from_inspection(inspection, reused=not result.created)


class SourceCapabilityUnavailable(RuntimeError):
    """A source operation is intentionally gated by its external evidence."""


def _source_for_environment(
    settings: Settings,
) -> tuple[Literal["demo", "real"], Literal["synthetic", "datajud"]]:
    if settings.environment == "real":
        return "real", "datajud"
    return "demo", "synthetic"


def _summary_from_row(job: Job) -> JobSummaryResponse:
    if job.collection_id is None:  # pragma: no cover - list query filters local rule runs
        raise RuntimeError("Um job de coleta não possui coleta associada.")
    return JobSummaryResponse(
        id=job.id,
        collection_id=job.collection_id,
        kind=cast(Any, job.job_type),
        environment=cast(Any, job.mode),
        source=job.source,
        tribunal=job.tribunal,
        status=cast(JobStatus, job.status),
        coverage=job.coverage,
        reason=job.reason,
        cancel_requested=job.cancel_requested,
        attempt_count=job.attempt_count,
        retry_cycle=job.retry_cycle,
        next_attempt_at=job.next_attempt_at,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _detail_from_inspection(inspection: JobInspection) -> JobDetailResponse:
    if inspection.collection_id is None:  # pragma: no cover - ruled out by route guard
        raise RuntimeError("Um job de coleta não possui coleta associada.")
    parameters = inspection.parameters_snapshot
    raw_query = parameters.get("query")
    query = raw_query if isinstance(raw_query, dict) else {}
    allowed_criteria = {
        "tribunal",
        "filed_from",
        "filed_to",
        "class_codes",
        "subject_codes",
        "movement_codes",
        "court_unit_code",
        "preset_id",
        "preset_version",
        "process_number",
        "catalog_snapshot",
    }
    criteria = {key: value for key, value in query.items() if key in allowed_criteria}
    raw_execution = parameters.get("parameters")
    execution = raw_execution if isinstance(raw_execution, dict) else {}
    return JobDetailResponse(
        id=inspection.id,
        collection_id=inspection.collection_id,
        kind=cast(Any, inspection.job_type),
        environment=cast(Any, inspection.mode),
        source=inspection.source,
        tribunal=inspection.tribunal,
        status=cast(JobStatus, inspection.status),
        coverage=inspection.coverage,
        reason=inspection.reason,
        cancel_requested=inspection.cancel_requested,
        attempt_count=inspection.attempt_count,
        retry_cycle=inspection.retry_cycle,
        next_attempt_at=inspection.next_attempt_at,
        created_at=inspection.created_at,
        started_at=inspection.started_at,
        finished_at=inspection.finished_at,
        criteria=criteria,
        execution=execution,
        checkpoint=JobCheckpointResponse(
            next_page=inspection.checkpoint.next_page,
            revision=inspection.checkpoint.revision,
            updated_at=inspection.checkpoint.updated_at,
        ),
        cursor_invalid=inspection.cursor_invalid,
        predecessor_job_id=inspection.predecessor_job_id,
        attempts=[
            JobAttemptResponse(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                outcome=attempt.outcome,
                error_code=attempt.error_code,
                error_summary=attempt.error_summary,
            )
            for attempt in inspection.attempts
        ],
        events=[
            JobEventResponse(
                event_number=event.event_number,
                event_type=event.event_type,
                details=event.details,
                created_at=event.created_at,
            )
            for event in inspection.events
        ],
    )


def _command_from_inspection(
    inspection: JobInspection,
    *,
    reused: bool,
) -> JobCommandResponse:
    if inspection.collection_id is None:  # pragma: no cover - ruled out by route guard
        raise RuntimeError("Um job de coleta não possui coleta associada.")
    return JobCommandResponse(
        job_id=inspection.id,
        collection_id=inspection.collection_id,
        status=cast(JobStatus, inspection.status),
        reused=reused,
        cancel_requested=inspection.cancel_requested,
        cursor_invalid=inspection.cursor_invalid,
    )


def _require_collection_job(inspection: JobInspection) -> None:
    if inspection.job_type not in ("discovery", "refresh_number"):
        raise HTTPException(status_code=404, detail="O job de coleta solicitado não existe.")
