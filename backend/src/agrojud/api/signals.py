"""HTTP endpoints for local rule reprocessing and process signal provenance."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    ProcessSignalResponse,
    ProcessSignalsResponse,
    SignalRunAcceptedResponse,
    SignalRunRequest,
    SignalRunStatusResponse,
)
from agrojud.db.models import Job, Process, ProcessSignal, SignalRunProcess
from agrojud.domain.signals import (
    InactiveSignalRuleError,
    SignalEnvironment,
    UnknownSignalRuleError,
)
from agrojud.services.signal_reprocessing import (
    SignalReprocessingService,
    SignalRunError,
    SignalSelectionError,
)

router = APIRouter(tags=["signals"])


@router.post(
    "/api/v1/rule-runs",
    response_model=SignalRunAcceptedResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Enfileira reprocessamento local de regras",
)
def create_rule_run(
    body: SignalRunRequest,
    request: Request,
    response: Response,
) -> SignalRunAcceptedResponse:
    service: SignalReprocessingService = request.app.state.signal_reprocessing
    environment = _environment(request)
    process_number = body.filters.process_number if body.filters is not None else None
    try:
        job_id, created, process_count, input_count = service.enqueue(
            environment=environment,
            process_ids=body.process_ids,
            process_number=process_number,
            rule_ids=body.rule_ids,
        )
    except UnknownSignalRuleError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SignalSelectionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InactiveSignalRuleError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    inspection = service.inspect(job_id, environment=environment)
    response.headers["Location"] = str(request.url_for("get_rule_run", job_id=str(job_id)))
    return SignalRunAcceptedResponse(
        job_id=job_id,
        status=cast(Any, inspection["status"]),
        process_count=process_count,
        input_count=input_count,
        reused=not created,
    )


@router.get(
    "/api/v1/rule-runs/{job_id}",
    name="get_rule_run",
    response_model=SignalRunStatusResponse,
    responses=ERROR_RESPONSES,
    summary="Consulta progresso do reprocessamento local",
)
def get_rule_run(job_id: UUID, request: Request) -> SignalRunStatusResponse:
    service: SignalReprocessingService = request.app.state.signal_reprocessing
    try:
        return SignalRunStatusResponse.model_validate(
            service.inspect(job_id, environment=_environment(request))
        )
    except SignalRunError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/api/v1/rule-runs/{job_id}/resume",
    response_model=SignalRunAcceptedResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Retoma execução local interrompida",
)
def resume_rule_run(
    job_id: UUID,
    request: Request,
    response: Response,
) -> SignalRunAcceptedResponse:
    service: SignalReprocessingService = request.app.state.signal_reprocessing
    try:
        environment = _environment(request)
        resumed_id = service.resume(job_id, environment=environment)
    except SignalRunError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    inspection = service.inspect(resumed_id, environment=environment)
    response.headers["Location"] = str(request.url_for("get_rule_run", job_id=str(resumed_id)))
    return SignalRunAcceptedResponse(
        job_id=resumed_id,
        status=cast(Any, inspection["status"]),
        process_count=inspection["process_count"],
        input_count=inspection["input_count"],
        reused=resumed_id != job_id,
    )


@router.get(
    "/api/v1/processes/{process_id}/signals",
    response_model=ProcessSignalsResponse,
    responses=ERROR_RESPONSES,
    summary="Lista sinais vigentes e históricos com proveniência",
)
def list_process_signals(
    process_id: UUID,
    request: Request,
    include_history: bool = Query(default=True),
) -> ProcessSignalsResponse:
    environment = _environment(request)
    with request.app.state.session_factory() as session:
        if session.get(Process, process_id) is None:
            raise HTTPException(status_code=404)
        statement = select(ProcessSignal).where(
            ProcessSignal.process_id == process_id,
            ProcessSignal.environment == environment,
            ProcessSignal.is_published.is_(True),
        )
        if not include_history:
            statement = statement.where(ProcessSignal.is_current.is_(True))
        signals = session.scalars(
            statement.order_by(
                ProcessSignal.is_current.desc(),
                ProcessSignal.evaluated_at.desc(),
                ProcessSignal.category.asc(),
                ProcessSignal.id.asc(),
            )
        ).all()
        latest_run_id = session.scalar(
            select(Job.id)
            .join(SignalRunProcess, SignalRunProcess.job_id == Job.id)
            .where(
                SignalRunProcess.process_id == process_id,
                Job.job_type == "reprocess_rules",
                Job.mode == environment,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        jobs_by_id: dict[UUID, Job] = {}
        if signals:
            jobs_by_id = {
                job.id: job
                for job in session.scalars(
                    select(Job).where(
                        Job.id.in_(
                            {signal.published_run_id or signal.created_run_id for signal in signals}
                        )
                    )
                ).all()
            }

    latest_run = None
    if latest_run_id is not None:
        service: SignalReprocessingService = request.app.state.signal_reprocessing
        latest_run = SignalRunStatusResponse.model_validate(
            service.inspect(latest_run_id, environment=environment)
        )

    return ProcessSignalsResponse(
        process_id=process_id,
        items=[
            ProcessSignalResponse(
                id=signal.id,
                state="current" if signal.is_current else "historical",
                evidence_stale=signal.evidence_stale,
                category=signal.category,
                rule_id=signal.rule_id,
                rule_version=signal.rule_version,
                rule_name=_rule_name(signal, jobs_by_id),
                explanation=signal.explanation,
                environment=cast(Any, signal.environment),
                rule_enablement=signal.enablement_snapshot,
                representation_id=signal.representation_id,
                evidence_kind=signal.evidence_kind,
                evidence_id=signal.evidence_id,
                movement_occurrence_id=signal.movement_occurrence_id,
                evidence_version_id=signal.evidence_version_id,
                evidence_snapshot_id=signal.evidence_snapshot_id,
                run_id=signal.published_run_id or signal.created_run_id,
                evaluated_at=signal.evaluated_at,
            )
            for signal in signals
        ],
        latest_run=latest_run,
    )


def _rule_name(signal: ProcessSignal, jobs: dict[UUID, Job]) -> str:
    job = jobs.get(signal.published_run_id or signal.created_run_id)
    rules = job.parameters_snapshot.get("rules") if job is not None else None
    if isinstance(rules, list):
        for item in rules:
            if (
                isinstance(item, dict)
                and item.get("id") == signal.rule_id
                and item.get("version") == signal.rule_version
                and isinstance(item.get("name"), str)
            ):
                return cast(str, item["name"])
    return signal.rule_id


def _environment(request: Request) -> SignalEnvironment:
    return "real" if request.app.state.settings.environment == "real" else "demo"
