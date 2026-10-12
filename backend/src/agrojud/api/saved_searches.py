"""Saved-search commands and schedule status for the local operator UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.job_groups import _group_response
from agrojud.api.jobs import SourceCapabilityUnavailable, _source_for_environment
from agrojud.api.schedule_status import schedule_dispatch_response
from agrojud.api.schemas import (
    ExecutionGroupResponse,
    JobCreatedResponse,
    JobStatus,
    SavedSearchAvailabilityResponse,
    SavedSearchCreateRequest,
    SavedSearchFilters,
    SavedSearchPatchRequest,
    SavedSearchResponse,
)
from agrojud.db.models import SavedSearch, SavedSearchVersion, ScheduleDispatch
from agrojud.services.execution_groups import execution_group_idempotency_key
from agrojud.services.saved_searches import (
    SavedSearchNotFoundError,
    availability_reasons,
    request_for_revision,
)
from agrojud.sources.contracts import SourceError, SourceErrorCode
from agrojud.sources.factory import real_source_unavailable_reason
from agrojud.sources.tribunal_availability import tribunal_availability

router = APIRouter(prefix="/api/v1/saved-searches", tags=["saved-searches"])


@router.post(
    "",
    response_model=SavedSearchResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
    summary="Salva uma busca versionada e agenda sua próxima atualização",
)
def create_saved_search(body: SavedSearchCreateRequest, request: Request) -> SavedSearchResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = request.app.state.saved_searches.create(
                session,
                name=body.name,
                preset_id=body.preset_id,
                window_mode=body.window_mode,
                filters=body.filters.model_dump(),
                enabled=body.enabled,
                mode=mode,
            )
            response = _search_response(
                session,
                search,
                revision,
                request=request,
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
    except SourceError:
        raise
    return response


@router.get(
    "",
    response_model=list[SavedSearchResponse],
    responses=ERROR_RESPONSES,
    summary="Lista buscas salvas, versão atual e situação da agenda",
)
def list_saved_searches(request: Request) -> list[SavedSearchResponse]:
    mode, source = _source_for_environment(request.app.state.settings)
    with request.app.state.session_factory() as session:
        searches = session.scalars(
            select(SavedSearch).order_by(SavedSearch.created_at.desc(), SavedSearch.id)
        ).all()
        return [
            _search_response(
                session,
                search,
                request.app.state.saved_searches.current_revision(session, search),
                request=request,
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
            for search in searches
        ]


@router.patch(
    "/{search_id}",
    response_model=SavedSearchResponse,
    responses=ERROR_RESPONSES,
    summary="Altera uma busca salva sem reescrever revisões anteriores",
)
def patch_saved_search(
    search_id: UUID,
    body: SavedSearchPatchRequest,
    request: Request,
) -> SavedSearchResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    fields = body.model_fields_set
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = request.app.state.saved_searches.patch(
                session,
                search_id,
                name=body.name if "name" in fields else None,
                preset_id=body.preset_id if "preset_id" in fields else None,
                window_mode=body.window_mode if "window_mode" in fields else None,
                filters=body.filters.model_dump() if body.filters is not None else None,
                enabled=body.enabled if "enabled" in fields else None,
                mode=mode,
            )
            response = _search_response(
                session,
                search,
                revision,
                request=request,
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
    except SavedSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca salva não existe.") from None
    return response


@router.post(
    "/{search_id}/run",
    response_model=JobCreatedResponse | ExecutionGroupResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Inicia explicitamente uma execução manual da busca salva",
)
def run_saved_search(
    search_id: UUID,
    request: Request,
    response: Response,
) -> JobCreatedResponse | ExecutionGroupResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    if request.app.state.settings.datajud_multitribunal_enabled:
        unavailable = real_source_unavailable_reason(request.app.state.settings)
        if unavailable is not None:
            raise SourceCapabilityUnavailable(unavailable)
        try:
            with request.app.state.session_factory() as session, session.begin():
                search = session.get(SavedSearch, search_id)
                if search is None:
                    raise SavedSearchNotFoundError("A busca salva não existe.")
                revision = request.app.state.saved_searches.current_revision(session, search)
                filters = dict(revision.filters)
                tribunals = filters.get("tribunals", ["TJGO"])
                if not isinstance(tribunals, list):
                    raise SourceError(
                        SourceErrorCode.CONTRACT,
                        "A lista de tribunais da revisão salva é inválida.",
                    )
                now = datetime.now(UTC)
                member_requests = {
                    tribunal: request_for_revision(
                        revision,
                        search_id=search.id,
                        mode=mode,
                        source=source,
                        reference_time=now,
                        tribunal=tribunal,
                    )
                    for tribunal in tribunals
                    if isinstance(tribunal, str)
                }
                if len(member_requests) != len(tribunals):
                    raise SourceError(
                        SourceErrorCode.CONTRACT,
                        "A lista de tribunais da revisão salva é inválida.",
                    )
                created = request.app.state.execution_groups.create_in_session(
                    session,
                    kind="discovery",
                    mode=mode,
                    source=source,
                    tribunals=tribunals,
                    preset_id=revision.preset_id,
                    request_snapshot={
                        "saved_search_id": str(search.id),
                        "saved_search_version": revision.version,
                        "criteria": filters,
                    },
                    request_for_tribunal=lambda tribunal: member_requests[tribunal],
                    idempotency_key=execution_group_idempotency_key(
                        target_id=search.id,
                        kind="discovery",
                        requests=tuple(member_requests.values()),
                    ),
                    allow_blocked=True,
                )
                view = request.app.state.execution_groups.inspect_in_session(
                    session, created.group_id
                )
                if view is None:  # pragma: no cover - inserted in this transaction
                    raise RuntimeError("O grupo criado não pôde ser lido.")
                group_response = _group_response(view)
        except SavedSearchNotFoundError:
            raise HTTPException(status_code=404, detail="A busca salva não existe.") from None
        response.headers["Location"] = str(
            request.url_for("get_job_group", group_id=str(group_response.id))
        )
        return group_response

    try:
        with request.app.state.session_factory() as session, session.begin():
            result = request.app.state.saved_searches.manual_run(
                session, search_id, mode=mode, source=source
            )
    except SavedSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca salva não existe.") from None
    inspection = request.app.state.jobs.inspect(result.job_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(result.job_id)))
    return JobCreatedResponse(
        job_id=result.job_id,
        collection_id=result.collection_id,
        status=cast(JobStatus, inspection.status),
        reused=not result.created,
    )


def _search_response(
    session: Session,
    search: SavedSearch,
    revision: SavedSearchVersion,
    *,
    request: Request,
    mode: str,
    source: str,
    reference_time: datetime,
) -> SavedSearchResponse:
    dispatch = session.scalar(
        select(ScheduleDispatch)
        .where(ScheduleDispatch.saved_search_id == search.id)
        .order_by(
            ScheduleDispatch.scheduled_for_date.desc(),
            ScheduleDispatch.created_at.desc(),
            ScheduleDispatch.id.desc(),
        )
        .limit(1)
    )
    reasons = availability_reasons(
        revision,
        search_id=search.id,
        mode=cast(Any, mode),
        source=source,
        reference_time=reference_time,
    )
    return SavedSearchResponse(
        id=search.id,
        name=search.name,
        version=revision.version,
        preset_id=revision.preset_id,
        preset_version=revision.preset_version,
        window_mode=cast(Any, revision.window_mode),
        filters=SavedSearchFilters.model_validate(revision.filters),
        enabled=search.enabled,
        next_run_at=search.next_run_at,
        availability=_saved_search_availability(
            request=request,
            revision=revision,
            search_id=search.id,
            mode=mode,
            source=source,
            reference_time=reference_time,
            reasons=reasons,
        ),
        last_dispatch=schedule_dispatch_response(dispatch),
        created_at=search.created_at,
        updated_at=search.updated_at,
    )


def _saved_search_availability(
    *,
    request: Request,
    revision: SavedSearchVersion,
    search_id: UUID,
    mode: str,
    source: str,
    reference_time: datetime,
    reasons: list[str],
) -> SavedSearchAvailabilityResponse:
    saved_tribunals = revision.filters.get("tribunals", ["TJGO"])
    if not isinstance(saved_tribunals, list):
        return SavedSearchAvailabilityResponse(
            enabled=False,
            reasons=["A lista de tribunais da revisão salva é inválida."],
        )
    multitribunal_active = (
        request.app.state.settings.datajud_multitribunal_enabled and source == "datajud"
    )
    if not multitribunal_active:
        if len(saved_tribunals) != 1:
            return SavedSearchAvailabilityResponse(
                enabled=False,
                reasons=["A seleção multitribunal não está ativa neste ambiente."],
            )
        return SavedSearchAvailabilityResponse(enabled=not reasons, reasons=reasons)
    gate = tribunal_availability(request.app.state.settings, preset_id=revision.preset_id)
    by_tribunal = {item.tribunal: item for item in gate.tribunals}
    gate_reasons = []
    for tribunal in saved_tribunals:
        capability = by_tribunal.get(str(tribunal).upper())
        if capability is None:
            gate_reasons.append(f"{tribunal}: tribunal fora da allowlist versionada.")
        elif not capability.enabled:
            gate_reasons.append(f"{tribunal}: {capability.reason or 'capacidade indisponível'}")
    combined = list(dict.fromkeys([*reasons, *gate.global_reasons, *gate_reasons]))
    return SavedSearchAvailabilityResponse(
        enabled=(
            not reasons
            and gate.selection_enabled
            and any(
                by_tribunal.get(str(tribunal).upper()) is not None
                and by_tribunal[str(tribunal).upper()].enabled
                for tribunal in saved_tribunals
            )
        ),
        reasons=combined,
    )
